"""会員の申し込み（Company Note の中で決済を作る）。2026-09-12。

なぜここで作るか:
    これまでは、公開の段（¥4,980）も招待の段（¥11,000）も、会員案内から
    gia2018.com の申込ページへ送っていた。あちらはセミナーや人の繋がりから
    来た人に向けた別のページで、しかも別ドメインなので Cookie が別＝
    Company Note にログイン済みの人にも**ログインし直し**を求めていた。
    申し込みはこのアプリの中で終わらせ、支払いのときだけ Stripe へ出る。

GIA側は何も変えない:
    - Stripe のアカウントも価格も同じもの（GIA4980 / GIA11000）を使う
    - 会員の印（applicants.plan）を書くのは、これまで通り gia-next の webhook

    ⚠️ **metadata は gia-next の webhook が読む形に必ずそろえる**
       （app/api/stripe/webhook/route.ts の purpose==='membership' の分岐）:
           purpose = 'membership'
           plan    = 'online' | 'invite'
           user_id = auth.users.id（applicants.id と同じ）
       1つでも欠けると「課金は通ったのに会員にならない」になる。
    ⚠️ **subscription 側にも同じ metadata を持たせる。** 更新・解約は session
       ではなく subscription が飛んでくるため、無いと誰の契約か分からなくなる。

支払い画面の出し方:
    いまは Stripe のページへ送る（hosted）。公開キー（pk_live_）が用意できたら
    embedded=True で自分のページに埋め込める。作る処理は同じで、渡す引数だけ違う。
"""

import hashlib
import json
import os
import threading
from datetime import datetime, timezone

import gia_identity

# 段ごとの価格。金額そのものは Stripe の Price が正本で、ここには持たない。
# ⚠️ 内部キー（online / invite）は webhook と applicants.plan の値なので変えない。
PLAN_PRICE_ENV = {
    'online': 'STRIPE_PRICE_ONLINE',
    'invite': 'STRIPE_PRICE_INVITE',
}
SECRET_ENV = 'STRIPE_SECRET_KEY'
PUBLISHABLE_ENV = 'STRIPE_PUBLISHABLE_KEY'

_lock = threading.Lock()


class CheckoutUnavailable(Exception):
    """いま決済を始められない（設定漏れ・Stripe側の失敗・状態が読めない）。"""


class AlreadyMember(Exception):
    """すでに契約中。二重に契約させない。"""


def publishable_key() -> str:
    """ブラウザ側で使う公開キー。埋め込み表示にするときだけ要る。

    ⚠️ 秘密キー（sk_）は絶対にブラウザへ渡さない。渡すと誰でも決済も返金もできる。
    """
    return (os.getenv(PUBLISHABLE_ENV) or '').strip()


def _client():
    key = (os.getenv(SECRET_ENV) or '').strip()
    if not key:
        raise CheckoutUnavailable('%s が未設定です' % SECRET_ENV)
    import stripe
    stripe.api_key = key
    return stripe


def _price_id(plan: str) -> str:
    env_name = PLAN_PRICE_ENV.get(plan)
    if not env_name:
        raise CheckoutUnavailable('知らない段です: %s' % plan)
    price = (os.getenv(env_name) or '').strip()
    if not price:
        raise CheckoutUnavailable('%s が未設定です' % env_name)
    return price


def _customer_id(user_id: str):
    """GIA側に控えてある Stripe の顧客番号。

    使い回さないと請求先が分かれ、同じ人が2人の顧客として並ぶ。
    読めなくても決済は始められるので、ここでは止めない。
    """
    try:
        client = gia_identity.get_admin_client()
        result = (client.table('applicants').select('stripe_customer_id')
                  .eq('id', user_id).limit(1).execute())
    except Exception as e:
        print('Stripe顧客番号の取得に失敗 %s: %s' % (user_id, str(e)[:150]))
        return None
    rows = result.data or []
    return (rows[0].get('stripe_customer_id') or None) if rows else None


def create_checkout(plan: str, user_id: str, email: str,
                    success_url: str, cancel_url: str) -> str:
    """Stripe の支払いページを作り、その URL を返す。

    Raises:
        AlreadyMember: すでに契約中
        CheckoutUnavailable: 設定漏れ・Stripeの失敗・会員状態が読めないとき
    """
    session = _create(plan, user_id, email,
                      {'success_url': success_url, 'cancel_url': cancel_url})
    url = session.get('url')
    if not url:
        raise CheckoutUnavailable('決済の準備ができませんでした')
    return url


def create_embedded_checkout(plan: str, user_id: str, email: str,
                             return_url: str) -> str:
    """自分のページに埋め込む形。client_secret を返す（公開キーが要る）。"""
    session = _create(plan, user_id, email,
                      {'ui_mode': 'embedded', 'return_url': return_url})
    secret = session.get('client_secret')
    if not secret:
        raise CheckoutUnavailable('決済の準備ができませんでした')
    return secret


def _create(plan: str, user_id: str, email: str, extra: dict):
    if not user_id:
        raise CheckoutUnavailable('ログインしていません')

    # ⚠️ **状態が読めないときは始めない。** 契約中かどうか分からないまま作ると、
    #    同じ人に2本の契約が立ち、解約時にどちらが残るか分からなくなる。
    #    二重課金の後始末は、売り逃しよりずっと高くつく。
    membership = gia_identity.get_membership(user_id)
    if membership.get('error'):
        raise CheckoutUnavailable('会員情報を確認できませんでした')
    if membership.get('plan') and membership.get('subscription_status') == 'active':
        raise AlreadyMember()

    stripe = _client()
    metadata = {'purpose': 'membership', 'plan': plan, 'user_id': user_id}
    customer = _customer_id(user_id)

    params = {
        'mode': 'subscription',
        'line_items': [{'price': _price_id(plan), 'quantity': 1}],
        'metadata': metadata,
        'subscription_data': {'metadata': metadata},
        'allow_promotion_codes': True,
        'locale': 'ja',
    }
    params.update(extra)
    if customer:
        params['customer'] = customer
    elif email:
        params['customer_email'] = email

    key = _idempotency_key(plan, user_id, params)
    try:
        with _lock:
            return stripe.checkout.Session.create(idempotency_key=key, **params)
    except CheckoutUnavailable:
        raise
    except Exception as e:
        message = str(e)
        # ⚠️ **鍵が衝突しても、その人を締め出さない。**
        #    Stripeは「同じ鍵は同じ引数のときだけ」という決まり。2026-09-12、
        #    鍵を日付で区切っていたため、戻り先の作りを変えた日に、同じ人が
        #    その日いっぱい申し込めなくなった（本番で発生）。
        #    引数から鍵を作るようにしたうえで、万一衝突したら鍵なしで作り直す。
        if 'idempotent' in message.lower():
            print('決済セッションの鍵が衝突したので作り直します: %s' % message[:200])
            try:
                with _lock:
                    return stripe.checkout.Session.create(**params)
            except Exception as retry_error:
                message = str(retry_error)
            else:
                pass
        # 価格の無効化・鍵の不備・通信失敗。生の例外文を画面に出さない。
        print('決済セッションの作成に失敗 %s: %s' % (user_id, message[:300]))
        raise CheckoutUnavailable('決済を開始できませんでした')


def _idempotency_key(plan, user_id, params):
    """連打・再読み込みでセッションが増えないようにする鍵。

    ⚠️ **引数の内容を鍵に含める。** Stripeは「同じ鍵は同じ引数のときだけ」と
       決めているので、日付だけで区切ると、戻り先などを変えた日に同じ人が
       一日中申し込めなくなる（2026-09-12 本番で発生）。
    ⚠️ 日付も残す。同じ引数でも、期限切れのセッションを翌日まで使い回さない。
    """
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    fingerprint = hashlib.sha256(
        json.dumps(params, sort_keys=True, default=str).encode('utf-8')
    ).hexdigest()[:16]
    return 'checkout:membership:%s:%s:%s:%s' % (plan, user_id, today, fingerprint)


def session_is_paid(session_id: str) -> bool:
    """完了ページで使う。その決済が支払い済みかを Stripe に問い合わせる。

    ⚠️ 会員かどうかの正本はここではない（webhook が applicants.plan に書く）。
       ここで見るのは「支払いが終わったか」だけ。
    """
    if not session_id or not session_id.startswith('cs_'):
        return False
    try:
        stripe = _client()
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception as e:
        print('決済セッションの確認に失敗: %s' % str(e)[:200])
        return False
    return session.get('payment_status') in ('paid', 'no_payment_required')
