# -*- coding: utf-8 -*-
"""TDnetの決算短信から通期予想を取る（2026-09-03）。

## なぜ作ったか

業績予想の取得元が Yahoo!ファイナンス日本版のHTMLだけで、いちばん脆い経路に
乗っていた（充足率83.6%）。決算短信は**会社が自分で出した一次情報**で、
TDnetがログイン不要・無料で配っている。

実物60本で突き合わせた結果、DBと比較できた27本のうち**25本が完全一致**。
ずれた2本は**どちらもTDnetが正しく、DBが古かった**。

⚠️ **TDnetは直近31日ぶんしか公開されていない。** 取りこぼした日は取り返せない
   ので毎日走らせる。過去に遡って一気に埋める道は無い。

## いちばん間違えやすいところ

短信サマリーには**似て非なるコンテキストが並ぶ**。前方一致で拾うと別の数字が
混ざる。実測で最多のタグは配当予想の期別内訳（DividendPerShare が65回）で、
`NextYearDuration_FirstQuarterMember_NonConsolidatedMember_ForecastMember`
のような名前を持つ。∴ **通期予想のコンテキストは完全一致で見る。**
"""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('ENABLE_SCHEDULER', 'false')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import tdnet  # noqa: E402
import tdnet_forecast  # noqa: E402


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def ix(name, ctx, value, scale='6', sign=None):
    return ('<ix:nonFraction name="tse-ed-t:%s" contextRef="%s" '
            'unitRef="JPY" scale="%s"%s>%s</ix:nonFraction>'
            % (name, ctx, scale, ' sign="-"' if sign else '', value))


def context(cid, end):
    return ('<xbrli:context id="%s"><xbrli:period>'
            '<xbrli:startDate>2026-04-01</xbrli:startDate>'
            '<xbrli:endDate>%s</xbrli:endDate>'
            '</xbrli:period></xbrli:context>' % (cid, end))


FULL = 'NextYearDuration_ConsolidatedMember_ForecastMember'
QUARTER = 'NextYearDuration_FirstQuarterMember_NonConsolidatedMember_ForecastMember'
HALF = 'NextAccumulatedQ2Duration_ConsolidatedMember_ForecastMember'
RESULT = 'CurrentYearDuration_ConsolidatedMember_ResultMember'


class 予想の取り出し(unittest.TestCase):

    def test_通期予想を億円で返す(self):
        html = (context(FULL, '2027-03-31')
                + ix('NetSales', FULL, '80,000')
                + ix('OperatingIncome', FULL, '5,700')
                + ix('OrdinaryIncome', FULL, '6,000')
                + ix('ProfitAttributableToOwnersOfParent', FULL, '4,300'))
        got = tdnet.extract_forecast(html)
        self.assertEqual(800.0, got['forecast_revenue'])       # 800億
        self.assertEqual(57.0, got['forecast_op_income'])
        self.assertEqual(60.0, got['forecast_ordinary_income'])
        self.assertEqual(43.0, got['forecast_net_income'])
        self.assertEqual('2027-03-31', got['forecast_year'])

    def test_配当の期別内訳を拾わない(self):
        """⚠️ 実測でいちばん多いタグ。前方一致で拾うと数字が混ざる。"""
        html = (context(FULL, '2027-03-31')
                + ix('DividendPerShare', QUARTER, '25', scale='0')
                + ix('NetSales', QUARTER, '99,999'))
        self.assertEqual({}, tdnet.extract_forecast(html))

    def test_中間期の予想を通期として拾わない(self):
        html = context(HALF, '2026-09-30') + ix('NetSales', HALF, '40,000')
        self.assertEqual({}, tdnet.extract_forecast(html))

    def test_実績を予想として拾わない(self):
        html = context(RESULT, '2026-03-31') + ix('NetSales', RESULT, '70,000')
        self.assertEqual({}, tdnet.extract_forecast(html))

    def test_赤字予想の符号(self):
        html = context(FULL, '2027-03-31') + ix('OperatingIncome', FULL, '1,200',
                                                sign=True)
        self.assertEqual(-12.0, tdnet.extract_forecast(html)['forecast_op_income'])

    def test_未定の欄は入れない(self):
        """「－」だけの欄を0にしない。0と未定はまるで違う。"""
        html = (context(FULL, '2027-03-31')
                + ix('NetSales', FULL, '80,000')
                + ix('OperatingIncome', FULL, '－'))
        got = tdnet.extract_forecast(html)
        self.assertIn('forecast_revenue', got)
        self.assertNotIn('forecast_op_income', got)

    def test_営業収益も売上として扱う(self):
        """銀行・鉄道・不動産などは NetSales でなく OperatingRevenues。"""
        html = context(FULL, '2027-03-31') + ix('OperatingRevenues', FULL, '35,600')
        self.assertEqual(356.0, tdnet.extract_forecast(html)['forecast_revenue'])

    def test_連結を非連結より優先する(self):
        non = 'NextYearDuration_NonConsolidatedMember_ForecastMember'
        html = (context(FULL, '2027-03-31') + context(non, '2027-03-31')
                + ix('NetSales', non, '10,000')
                + ix('NetSales', FULL, '80,000'))
        self.assertEqual(800.0, tdnet.extract_forecast(html)['forecast_revenue'])

    def test_予想が無ければ空(self):
        self.assertEqual({}, tdnet.extract_forecast(''))
        self.assertEqual({}, tdnet.extract_forecast('<html></html>'))


class 一覧の読み取り(unittest.TestCase):

    LIST = ('<table><tr><td>15:00</td><td>61180</td><td>アイダ</td>'
            '<td>2027年３月期第１四半期決算短信〔日本基準〕(連結)</td>'
            '<td><a href="081220260814500123.zip">XBRL</a></td></tr>'
            '<tr><td>15:30</td><td>34700</td><td>Ｒ－マリモ</td>'
            '<td>2026年6月期　決算短信（ＲＥＩＴ）</td>'
            '<td><a href="x.zip">XBRL</a></td></tr></table>')

    def test_行を読める(self):
        rows = tdnet.parse_list(self.LIST)
        self.assertEqual(2, len(rows))
        self.assertEqual('61180', rows[0]['code'])
        self.assertEqual('081220260814500123.zip', rows[0]['zip'])

    def test_REITは対象外(self):
        rows = tdnet.parse_list(self.LIST)
        self.assertTrue(tdnet.is_earnings_report(rows[0]['title']))
        self.assertFalse(tdnet.is_earnings_report(rows[1]['title']))

    def test_決算短信以外は対象外(self):
        self.assertFalse(tdnet.is_earnings_report('自己株式の取得状況に関するお知らせ'))

    def test_訂正版も対象にする(self):
        self.assertTrue(tdnet.is_earnings_report(
            '（訂正・数値データ訂正）「2027年３月期　第１四半期決算短信〔日本基準〕」'))

    def test_5桁コードを4桁にする(self):
        """⚠️ 一覧は末尾に種類の1桁が付く。screened_latest は4桁。"""
        self.assertEqual('6118', tdnet._four_digit('61180'))
        self.assertEqual('277A', tdnet._four_digit('277A0'))
        self.assertEqual('6118', tdnet._four_digit('6118'))

    def test_URLは日付から作る(self):
        self.assertTrue(tdnet.list_url(date(2026, 8, 14))
                        .endswith('I_list_001_20260814.html'))


class 書き込みの決まり(unittest.TestCase):

    def test_同じ期なら取れた項目だけ足す(self):
        existing = {'forecast_revenue': 100.0, 'forecast_op_income': 10.0,
                    'forecast_year': '2027-03-31'}
        got = tdnet_forecast.build_update(
            existing, {'forecast_revenue': 120.0, 'forecast_year': '2027-03-31'})
        self.assertEqual(120.0, got['forecast_revenue'])
        self.assertNotIn('forecast_op_income', got)   # 前の値をそのまま残す

    def test_期が変わったら取れなかった項目は消す(self):
        """⚠️ 「売上は今期・営業益は前期」を作らない。実際に内田洋行が
        この形だった（短信は売上だけ、DBには前期の営業益）。"""
        existing = {'forecast_revenue': 100.0, 'forecast_op_income': 10.0,
                    'forecast_year': '2026-07-20'}
        got = tdnet_forecast.build_update(
            existing, {'forecast_revenue': 400.0, 'forecast_year': '2027-07-20'})
        self.assertEqual(400.0, got['forecast_revenue'])
        self.assertIsNone(got['forecast_op_income'])
        self.assertEqual('2027-07-20', got['forecast_year'])

    def test_中身が同じなら書かない(self):
        existing = {'forecast_revenue': 100.0, 'forecast_year': '2027-03-31'}
        self.assertEqual({}, tdnet_forecast.build_update(
            existing, {'forecast_revenue': 100.0, 'forecast_year': '2027-03-31'}))

    def test_予想が空なら何もしない(self):
        self.assertEqual({}, tdnet_forecast.build_update({'forecast_revenue': 1}, {}))


class 上書きガード(unittest.TestCase):
    """⚠️ Yahooの予想は短信の写しで、期が変わった直後は古い期のまま。
    そのまま上書きすると、一次情報で入れた値が古い数字に戻る。"""

    def setUp(self):
        import supabase_client
        self.keep = supabase_client.keep_disclosed_forecast

    def test_短信由来なら同じ期の上書きを弾く(self):
        existing = {'forecast_year': '2027-07-20',
                    'source_status': {'forecast': {'source': 'tdnet'}}}
        data = {'forecast_revenue': 421.0, 'forecast_year': '2026-07-20',
                'company_code': '8057'}
        got = self.keep(data, existing)
        self.assertNotIn('forecast_revenue', got)
        self.assertNotIn('forecast_year', got)
        self.assertEqual('8057', got['company_code'])   # 他の列は消さない

    def test_より新しい期なら通す(self):
        existing = {'forecast_year': '2027-03-31',
                    'source_status': {'forecast': {'source': 'tdnet'}}}
        data = {'forecast_revenue': 500.0, 'forecast_year': '2028-03-31'}
        self.assertEqual(500.0, self.keep(data, existing)['forecast_revenue'])

    def test_短信由来でなければ触らない(self):
        existing = {'forecast_year': '2027-03-31', 'source_status': {}}
        data = {'forecast_revenue': 500.0, 'forecast_year': '2026-03-31'}
        self.assertEqual(500.0, self.keep(data, existing)['forecast_revenue'])

    def test_保存の入口で必ず通る(self):
        """1か所でも素通りする道があると、そこから古い値に戻る。"""
        src = read('supabase_client.py')
        block = src.split('def upsert_screened_data_with_match_rate(', 1)[1]
        block = block.split('\ndef ', 1)[0]
        self.assertIn('keep_disclosed_forecast(data, existing)', block)


class 定期実行(unittest.TestCase):

    def setUp(self):
        self.src = read('app.py')

    def test_毎日登録されている(self):
        """⚠️ 直近31日しか公開されないので、週1では取りこぼす。"""
        self.assertIn("scheduled_fetch_tdnet_forecasts, 'cron', hour=20", self.src)
        block = self.src.split("scheduled_fetch_tdnet_forecasts, 'cron'", 1)[1]
        block = block.split(')', 1)[0]
        self.assertNotIn('day_of_week', block)

    def test_実行を記録する(self):
        block = self.src.split('def scheduled_fetch_tdnet_forecasts(', 1)[1]
        block = block.split('\ndef ', 1)[0]
        self.assertIn("record_job_run('tdnet_forecast'", block)

    def test_二重起動の門を通る(self):
        block = self.src.split('def scheduled_fetch_tdnet_forecasts(', 1)[1]
        block = block.split('\ndef ', 1)[0]
        self.assertIn("claim_job('tdnet_forecast')", block)

    def test_短信が無い日を失敗にしない(self):
        """休日明けや閑散期は0本が正常。"""
        block = self.src.split('def scheduled_fetch_tdnet_forecasts(', 1)[1]
        block = block.split('\ndef ', 1)[0]
        self.assertIn("ok=(stats['failed'] == 0)", block)

    def test_他のジョブと時間が重ならない(self):
        import re
        hours = re.findall(r"'cron', hour=(\d+), minute=(\d+)", self.src)
        slots = [(int(h), int(m)) for h, m in hours]
        self.assertEqual(1, slots.count((20, 0)), '20:00 に別のジョブがある')


if __name__ == '__main__':
    unittest.main()
