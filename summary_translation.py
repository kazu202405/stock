"""英語の事業概要を日本語へ要約する共通フォールバック。"""


def translate_summary_to_jp(english_text):
    """英語概要を事実ベースの短い日本語へ要約する。失敗時はNone。"""
    import llm

    if not english_text or not llm.is_available():
        return None
    try:
        prompt = (
            "以下は海外データベースに載っている企業の事業内容の説明文です。"
            "これを日本語で、事実だけを簡潔に要約してください。\n"
            "条件:\n"
            "- 200文字以内の平易な日本語\n"
            "- 何で稼いでいる会社かが分かるように書く\n"
            "- 投資判断（買い時・推奨など）は一切書かない\n"
            "- 要約文のみを出力し、前置きや見出しは付けない\n\n"
            f"{english_text[:3000]}"
        )
        text = llm.chat(prompt, model='gpt-4o-mini', temperature=0.2, timeout=30)
        return (text or '').strip() or None
    except Exception as exc:
        print(f"事業概要のLLM翻訳エラー: {exc}")
        return None
