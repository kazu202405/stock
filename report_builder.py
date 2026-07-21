"""
企業分析レポートの組み立て。

設計方針:
  レポートを「上場銘柄」に紐づけない。データ源が何であっても同じ構造を返し、
  描画側（templates/report_view.html）はその構造だけを知る。
  こうすることで、将来「経営者が自社の決算数値を入力してレポートを出す」を
  足すときに、データ源を1本足すだけで済む。

      screened_latest（上場企業）─┐
                                  ├→ build_report() が返す共通構造 → 共通レンダラ
      settlements（自社入力）    ─┘   ※ build_from_settlements() を後日追加

  数値部分はDBから都度組み立てる。文章部分はLLMで生成し stock_reports に保存する。
"""

import json
import hashlib
from datetime import datetime, timezone

NARRATIVE_MODEL = 'gpt-4o-mini'


# ---------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------

def _as_obj(value):
    """JSONB が文字列で返ってくる場合があるため吸収する"""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None
    return value


def _series(history, key):
    """financial_history / cf_history から [{date, value}] を取り出す"""
    if not isinstance(history, dict):
        return []
    rows = history.get(key) or []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        value = r.get('value')
        if value is None:
            continue
        out.append({'date': r.get('date'), 'value': value})
    # 古い順に並べる（DBは新しい順で入っていることがある）
    out.sort(key=lambda x: x.get('date') or '')
    return out


def _fiscal_label(date_str):
    """'2026-03-28' → '2026年3月'"""
    if not date_str:
        return ''
    try:
        d = datetime.fromisoformat(str(date_str)[:10])
        return f'{d.year}年{d.month}月'
    except Exception:
        return str(date_str)[:7]


def _oku(value):
    """円 → 億円（小数1桁）。すでに億円単位のカラムはそのまま渡さないこと。"""
    if value is None:
        return None
    return round(value / 1e8, 1)


# ---------------------------------------------------------------
# 上場企業（screened_latest）からの組み立て
# ---------------------------------------------------------------

def build_from_screened(row):
    """screened_latest の1行から共通のレポート構造を作る"""
    financial = _as_obj(row.get('financial_history')) or {}
    cf = _as_obj(row.get('cf_history')) or {}

    revenue = _series(financial, 'revenue')
    op_income = _series(financial, 'op_income')
    net_income = _series(financial, 'net_income')
    payout = _series(financial, 'payout_ratio')
    equity_ratio_hist = _series(cf, 'equity_ratio')
    roa_hist = _series(cf, 'roa')
    cash_hist = _series(cf, 'cash')
    op_cf_hist = _series(cf, 'operating_cf')

    # --- 売上高・営業利益の推移（実績4年 + 今期予想を5点目に足す） ---
    revenue_op = None
    if revenue:
        labels = [_fiscal_label(r['date']) for r in revenue]
        rev_values = [_oku(r['value']) for r in revenue]
        op_values = []
        op_map = {r['date']: r['value'] for r in op_income}
        for r in revenue:
            v = op_map.get(r['date'])
            op_values.append(_oku(v) if v is not None else None)

        # yfinanceの年次実績は4期しか返らないため、今期予想を5点目として補う
        forecast_added = False
        if row.get('forecast_revenue') is not None and row.get('forecast_year'):
            labels.append(f"{_fiscal_label(row['forecast_year'])}(予)")
            rev_values.append(row.get('forecast_revenue'))
            op_values.append(row.get('forecast_op_income'))
            forecast_added = True

        revenue_op = {
            'labels': labels,
            'revenue': rev_values,
            'op_income': op_values,
            'has_forecast': forecast_added,
            'unit': '億円',
        }

    # --- 自己資本比率・ROAの推移 ---
    equity_roa = None
    if equity_ratio_hist or roa_hist:
        roa_map = {r['date']: r['value'] for r in roa_hist}
        labels, eq_values, roa_values = [], [], []
        base = equity_ratio_hist or roa_hist
        for r in base:
            labels.append(_fiscal_label(r['date']))
            eq_values.append(round(r['value'], 1) if equity_ratio_hist else None)
            v = roa_map.get(r['date'])
            roa_values.append(round(v, 1) if v is not None else None)
        equity_roa = {'labels': labels, 'equity_ratio': eq_values, 'roa': roa_values, 'unit': '%'}

    # --- スナップショット（値がある項目だけ並べる） ---
    def item(label, value, unit='', digits=None):
        if value is None or value == '':
            return None
        if digits is not None:
            try:
                value = round(float(value), digits)
            except (TypeError, ValueError):
                pass
        return {'label': label, 'value': value, 'unit': unit}

    snapshot = [
        item('株価', row.get('stock_price'), '円', 0),
        item('業種', row.get('industry_jp') or row.get('sector')),
        item('時価総額', row.get('market_cap'), '億円', 0),
        item('自己資本比率', row.get('equity_ratio'), '%', 2),
        item('営業利益率', row.get('operating_margin'), '%', 2),
        item('PER', row.get('per_forward'), '倍', 2),
        item('PBR', row.get('pbr'), '倍', 2),
        item('配当利回り', row.get('dividend_yield'), '%', 2),
        item('信用倍率', row.get('margin_trading_ratio'), '倍', 2),
        item('設立', row.get('established')),
        item('従業員数', row.get('employees')),
    ]
    snapshot = [x for x in snapshot if x]

    # --- 財務の健全性 ---
    health = [
        item('現預金', row.get('cash'), '億円', 1),
        item('流動負債', row.get('current_liabilities'), '億円', 1),
        item('流動比率', row.get('current_ratio'), '%', 1),
        item('営業CF', row.get('operating_cf'), '億円', 1),
        item('配当性向', row.get('payout_ratio'), '%', 1),
        item('ROE', row.get('roe'), '%', 1),
        item('ROA', row.get('roa'), '%', 1),
        item('EPS', row.get('eps'), '円', 1),
    ]
    health = [x for x in health if x]

    # 現預金の推移（最初と最後だけ示す）
    cash_trend = None
    if len(cash_hist) >= 2:
        cash_trend = {
            'from': _oku(cash_hist[0]['value']),
            'to': _oku(cash_hist[-1]['value']),
            'from_label': _fiscal_label(cash_hist[0]['date']),
            'to_label': _fiscal_label(cash_hist[-1]['date']),
            'unit': '億円',
        }

    shareholders = _as_obj(row.get('major_shareholders_jp')) or []
    officers = _as_obj(row.get('company_officers')) or []

    # 代表者名: 専用カラム優先、無ければ役員一覧から「代表取締役」を拾う
    ceo = row.get('ceo_name')
    if not ceo and isinstance(officers, list):
        for o in officers:
            if isinstance(o, dict) and '代表取締役' in (o.get('title') or ''):
                ceo = o.get('name')
                break

    report = {
        'source': 'listed',
        'key': row.get('company_code'),
        'company': {
            'name': row.get('company_name'),
            'code': row.get('company_code'),
            'sector': row.get('sector'),
            'industry': row.get('industry_jp'),
            'ceo_name': ceo,
            'established': row.get('established'),
            'headquarters': row.get('headquarters'),
            'as_of': row.get('analyzed_at'),
        },
        'business_summary': row.get('business_summary_jp') or row.get('business_summary'),
        'summary_is_japanese': bool(row.get('business_summary_jp')),
        'match_rate': row.get('match_rate'),
        'snapshot': snapshot,
        'health': health,
        'cash_trend': cash_trend,
        'trends': {
            'revenue_op': revenue_op,
            'equity_roa': equity_roa,
            'payout': [{'label': _fiscal_label(p['date']), 'value': round(p['value'], 1)} for p in payout[-3:]],
        },
        'shareholders': [s for s in shareholders if isinstance(s, dict)][:5],
        'officers': [o for o in officers if isinstance(o, dict)][:5],
        'narrative': None,
    }

    # どのセクションが描けるか（テンプレート側の分岐を単純にするため）
    report['availability'] = {
        'business_summary': bool(report['business_summary']),
        'snapshot': bool(snapshot),
        'trends_revenue': bool(revenue_op),
        'trends_equity': bool(equity_roa),
        'health': bool(health),
        'shareholders': bool(report['shareholders']),
        'narrative_possible': bool(report['business_summary']),
    }
    return report


# ---------------------------------------------------------------
# 文章生成（LLM）とキャッシュ
# ---------------------------------------------------------------

def _input_fingerprint(report):
    """生成元データの指紋。これが変わったら文章を作り直す。"""
    seed = json.dumps({
        'summary': report.get('business_summary'),
        'snapshot': report.get('snapshot'),
        'revenue': (report.get('trends') or {}).get('revenue_op'),
        'health': report.get('health'),
    }, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(seed.encode('utf-8')).hexdigest()[:32]


def _build_prompt(report):
    company = report['company']
    lines = [f"企業名: {company.get('name')}（{company.get('code')}）"]
    if company.get('sector'):
        lines.append(f"業種: {company['sector']}")
    if report.get('business_summary'):
        lines.append(f"事業内容: {report['business_summary']}")
    if report.get('snapshot'):
        vals = '、'.join(f"{i['label']}{i['value']}{i['unit']}" for i in report['snapshot'])
        lines.append(f"主要指標: {vals}")
    rev = (report.get('trends') or {}).get('revenue_op')
    if rev:
        pairs = []
        for i, label in enumerate(rev['labels']):
            r = rev['revenue'][i]
            o = rev['op_income'][i]
            if r is not None:
                pairs.append(f"{label} 売上{r}億円/営業利益{o if o is not None else '-'}億円")
        if pairs:
            lines.append('業績推移: ' + '、'.join(pairs))
    if report.get('health'):
        vals = '、'.join(f"{i['label']}{i['value']}{i['unit']}" for i in report['health'])
        lines.append(f"財務: {vals}")

    return (
        "あなたは企業分析の解説者です。以下の公開情報から、学習用の分析メモを作成してください。\n\n"
        "厳守すること:\n"
        "- 投資判断を促す表現は一切書かない（買い時・売り時・推奨・狙い目・今が好機 などは禁止）\n"
        "- 与えられた情報から読み取れることだけを書く。数字を creative に作らない\n"
        "- 「何で稼いでいる会社か」「財務構造はどうか」を理解するための記述に徹する\n"
        "- 断定を避け、事実と解釈を区別する\n\n"
        "次のJSONのみを出力すること（前後に説明文を付けない）:\n"
        "{\n"
        '  "one_line": "この会社を一言で説明する文（100文字程度）",\n'
        '  "strengths": ["ビジネスモデル上の強み（各30文字程度）", "..."],\n'
        '  "risks": ["注意して見るべき点（各30文字程度）", "..."],\n'
        '  "learnings": ["この企業から学べること（各40文字程度）", "..."],\n'
        '  "closing": "数字と事業の両面からのまとめ（120文字程度）"\n'
        "}\n"
        "strengths/risks/learnings はそれぞれ3〜5項目。\n\n"
        "--- 対象企業の情報 ---\n" + "\n".join(lines)
    )


def generate_narrative(report):
    """LLMで文章部分を生成する。失敗時は None を返す（レポート自体は数値だけで成立する）。"""
    import llm
    if not llm.is_available() or not report.get('business_summary'):
        return None

    data = llm.chat_json(_build_prompt(report), model=NARRATIVE_MODEL, temperature=0.3)
    if not isinstance(data, dict):
        return None
    return {
        'one_line': data.get('one_line'),
        'strengths': [s for s in (data.get('strengths') or []) if s][:5],
        'risks': [s for s in (data.get('risks') or []) if s][:5],
        'learnings': [s for s in (data.get('learnings') or []) if s][:5],
        'closing': data.get('closing'),
    }


def get_cached_narrative(source, key, fingerprint):
    from supabase_client import get_supabase_client
    try:
        client = get_supabase_client()
        res = (client.table('stock_reports')
               .select('narrative, input_hash, generated_at')
               .eq('source', source).eq('source_key', key)
               .execute())
        if not res.data:
            return None
        row = res.data[0]
        # 元データが変わっていたら作り直す
        if row.get('input_hash') != fingerprint:
            return None
        return _as_obj(row.get('narrative'))
    except Exception as e:
        print(f"レポート文章のキャッシュ取得エラー: {e}")
        return None


def save_narrative(source, key, fingerprint, narrative):
    from supabase_client import get_supabase_client
    try:
        client = get_supabase_client()
        client.table('stock_reports').upsert({
            'source': source,
            'source_key': key,
            'narrative': narrative,
            'model': NARRATIVE_MODEL,
            'input_hash': fingerprint,
            'generated_at': datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        print(f"レポート文章の保存エラー: {e}")


def attach_narrative(report, regenerate=False):
    """レポートに文章を付ける。キャッシュがあれば使い、無ければ生成して保存する。"""
    if not report['availability']['narrative_possible']:
        return report

    fingerprint = _input_fingerprint(report)
    source, key = report['source'], report['key']

    if not regenerate:
        cached = get_cached_narrative(source, key, fingerprint)
        if cached:
            report['narrative'] = cached
            report['narrative_cached'] = True
            return report

    narrative = generate_narrative(report)
    if narrative:
        save_narrative(source, key, fingerprint, narrative)
        report['narrative'] = narrative
        report['narrative_cached'] = False
    return report


def build_report(source, key, regenerate=False):
    """データ源を指定してレポートを組み立てる共通の入口。

    将来 source='own'（自社決算）を足すときは、ここに分岐を1本追加し、
    screened と同じ構造を返す builder を書くだけでよい。
    """
    if source == 'listed':
        from supabase_client import get_screened_data
        row = get_screened_data(key)
        if not row:
            return None
        report = build_from_screened(row)
    else:
        raise ValueError(f'未対応のデータ源です: {source}')

    return attach_narrative(report, regenerate=regenerate)
