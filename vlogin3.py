import os, secrets, sys
os.environ.setdefault('ENABLE_SCHEDULER','false')
from dotenv import load_dotenv; load_dotenv()
import gia_identity
from supabase_client import get_supabase_client
email = f'e2e-{secrets.token_hex(4)}@example.com'
pw = secrets.token_urlsafe(16)
acct = gia_identity.create_auth_user(email, pw)
from app import app
app.config['TESTING'] = True
out = []
try:
    with app.test_client() as c:
        r = c.post('/login', data={'email': email, 'password': pw})
        out.append(f"POST /login       -> {r.status_code} {r.headers.get('Location')}")
        with c.session_transaction() as s:
            out.append(f"  user_id={(s.get('user_id') or '')[:8]} role={s.get('user_role')}")
        out.append(f"GET  /dashboard   -> {c.get('/dashboard').status_code}")
        out.append(f"GET  /admin/users -> {c.get('/admin/users').status_code} (302が正)")
    with app.test_client() as c2:
        bad = c2.post('/login', data={'email': email, 'password': 'wrong'})
        out.append(f"誤PW              -> {bad.status_code}")
        with c2.session_transaction() as s:
            out.append(f"  user_id={s.get('user_id')} (Noneが正)")
finally:
    gia_identity.get_admin_client().auth.admin.delete_user(acct['id'])
    get_supabase_client().table('app_users').delete().eq('id', acct['id']).execute()
    out.append('検証用アカウント削除')
print('\n'.join(out), flush=True)
sys.stdout.flush()
os._exit(0)
