import time 
import yfinance as yf
import yahooquery as yq
import pandas as pd

def is_empty_df(x):
    """DataFrameが空かどうかを安全にチェック"""
    return (x is None) or (isinstance(x, pd.DataFrame) and x.empty)

def probe_symbol(symbol: str):
    """株主・役員情報の取得可能性をテスト"""
    print(f"\n=== {symbol} ===")

    # --- yfinance ---
    print('--- yfinance ---')
    t = yf.Ticker(symbol)
    major = None
    inst = None
    mf = None
    
    try:
        major = t.major_holders
        print('major_holders:',
              'None' if major is None else f'DF rows={len(major) if hasattr(major,"__len__") else "?"}')
        if isinstance(major, pd.DataFrame) and not major.empty:
            print(major.head())
    except Exception as e:
        print(f'major_holders エラー: {e}')

    try:
        inst = t.institutional_holders
        print('institutional_holders:',
              'None' if inst is None else f'empty={getattr(inst,"empty",None)}')
        if isinstance(inst, pd.DataFrame) and not inst.empty:
            print(inst.head())
    except Exception as e:
        print(f'institutional_holders エラー: {e}')

    try:
        mf = t.mutualfund_holders
        print('mutualfund_holders:',
              'None' if mf is None else f'empty={getattr(mf,"empty",None)}')
        if isinstance(mf, pd.DataFrame) and not mf.empty:
            print(mf.head())
    except Exception as e:
        print(f'mutualfund_holders エラー: {e}')

    # --- yahooquery ---
    print('--- yahooquery ---')
    yt = yq.Ticker(symbol, formatted=False)
    officers = []

    try:
        ap = yt.asset_profile.get(symbol, {}) or {}
        officers = ap.get('companyOfficers') or []
        print(f'companyOfficers: {len(officers)} 人')
        for i, o in enumerate(officers[:3]):
            print(f'  {i+1}. {o.get("name","N/A")} - {o.get("title","N/A")}')
    except Exception as e:
        print(f'companyOfficers エラー: {e}')

    try:
        inst_own = yt.institution_ownership.get(symbol) or []
        print('institution_ownership:', 'あり' if inst_own else 'なし')
        for i, h in enumerate(inst_own[:3]):
            pct = h.get('pctHeld')
            print(f'  {i+1}. {h.get("organization","N/A")} - {pct*100:.2f}%' if pct is not None else '  ー')
    except Exception as e:
        print(f'institution_ownership エラー: {e}')

    # 追加で米株向けに有用（日本株は空のこと多い）
    try:
        brk = yt.major_holders_breakdown.get(symbol) or {}
        if brk: print('major_holders_breakdown: あり')
    except Exception as e:
        print(f'major_holders_breakdown エラー: {e}')

    try:
        fown = yt.fund_ownership.get(symbol) or []
        if fown: print(f'fund_ownership: {len(fown)}件')
    except Exception as e:
        print(f'fund_ownership エラー: {e}')

    # .T の場合はフォールバック判断
    if symbol.endswith('.T'):
        jp_need_fallback = (
            (major is None or (isinstance(major, pd.DataFrame) and major.empty)) and
            (is_empty_df(inst)) and
            (len(officers) == 0)
        )
        print('JP fallback needed:', jp_need_fallback)
        if jp_need_fallback:
            print('→ 主要株主/役員は Yahoo!日本版スクレイプ or EDINET で取得してください。')

def get_holders_and_officers(symbol: str):
    """主要株主・役員情報を取得（本番用）"""
    result = {
        "symbol": symbol,
        "major_holders": None,
        "institutional_holders": None,
        "company_officers": None,
        "institution_ownership": None,
        "fund_ownership": None,
        "major_holders_breakdown": None,
        "source": "yfinance/yahooquery",
        "fallback_needed": False
    }
    
    try:
        # yfinance
        ticker = yf.Ticker(symbol)
        
        # major_holders
        try:
            major = ticker.major_holders
            if isinstance(major, pd.DataFrame) and not major.empty:
                result["major_holders"] = major.to_dict('records')
        except:
            pass
        
        # institutional_holders
        try:
            inst = ticker.institutional_holders
            if isinstance(inst, pd.DataFrame) and not inst.empty:
                result["institutional_holders"] = inst.to_dict('records')
        except:
            pass
        
        time.sleep(0.3)  # 礼儀
        
        # yahooquery
        yq_ticker = yq.Ticker(symbol, formatted=False)
        
        # company_officers
        try:
            ap = yq_ticker.asset_profile.get(symbol, {}) or {}
            officers = ap.get('companyOfficers') or []
            if officers:
                result["company_officers"] = officers
        except:
            pass
        
        # institution_ownership
        try:
            inst_own = yq_ticker.institution_ownership.get(symbol) or []
            if inst_own:
                result["institution_ownership"] = inst_own
        except:
            pass
        
        # fund_ownership
        try:
            fund_own = yq_ticker.fund_ownership.get(symbol) or []
            if fund_own:
                result["fund_ownership"] = fund_own
        except:
            pass
        
        # major_holders_breakdown
        try:
            breakdown = yq_ticker.major_holders_breakdown.get(symbol) or {}
            if breakdown:
                result["major_holders_breakdown"] = breakdown
        except:
            pass
        
        # .T銘柄でデータが不足している場合はフォールバックフラグ
        if symbol.endswith('.T'):
            jp_need_fallback = (
                not result["major_holders"] and
                not result["institutional_holders"] and
                not result["company_officers"]
            )
            result["fallback_needed"] = jp_need_fallback
            if jp_need_fallback:
                result["source"] = "yfinance/yahooquery (fallback recommended)"
        
    except Exception as e:
        result["error"] = str(e)
    
    return result

if __name__ == "__main__":
    for s in ['7203.T', 'AAPL']:
        probe_symbol(s)
        time.sleep(0.3)  # 礼儀