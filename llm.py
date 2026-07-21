"""
OpenAI APIの呼び出しを1か所にまとめる。

背景:
  requirements.txt は openai==0.28.0 を指定しているが、ローカル環境には
  2.21.0 が入っていた。0.28 と 1.0 以降ではAPIが非互換で、
  0.28: openai.ChatCompletion.create(...)
  1.0+: client.chat.completions.create(...)
  と呼び方が変わる。どちらの環境でも動くようここで吸収する。

  （本来は requirements.txt を実態に合わせて統一すべきだが、既存の
    models/common.py が旧APIで動いているため、まずは両対応で安全側に倒す）
"""

DEFAULT_MODEL = 'gpt-4o-mini'


def is_available():
    from config import GPT_API
    return bool(GPT_API)


def chat(prompt, model=DEFAULT_MODEL, temperature=0.3, timeout=60):
    """プロンプトを投げて本文テキストを返す。失敗時は None。"""
    from config import GPT_API
    if not GPT_API:
        return None

    import openai
    messages = [{"role": "user", "content": prompt}]

    try:
        # openai >= 1.0
        if hasattr(openai, 'OpenAI'):
            client = openai.OpenAI(api_key=GPT_API, timeout=timeout)
            res = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
            )
            return (res.choices[0].message.content or '').strip()

        # openai < 1.0
        openai.api_key = GPT_API
        res = openai.ChatCompletion.create(
            model=model,
            messages=messages,
            temperature=temperature,
            request_timeout=timeout,
        )
        return (res['choices'][0]['message']['content'] or '').strip()
    except Exception as e:
        print(f'LLM呼び出しエラー: {e}')
        return None


def chat_json(prompt, model=DEFAULT_MODEL, temperature=0.3, timeout=60):
    """JSONを返させたいとき用。```json ...``` の囲みも取り除いてdictで返す。"""
    import json
    text = chat(prompt, model=model, temperature=temperature, timeout=timeout)
    if not text:
        return None
    if text.startswith('```'):
        parts = text.split('```')
        if len(parts) >= 2:
            text = parts[1]
            if text.startswith('json'):
                text = text[4:]
    try:
        return json.loads(text.strip())
    except Exception as e:
        print(f'LLM応答のJSON解析に失敗: {e}')
        return None
