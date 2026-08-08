import os, secrets
os.environ.setdefault('ENABLE_SCHEDULER','false')
from dotenv import load_dotenv; load_dotenv()
import gia_identity
from supabase_client import get_supabase_client
email = f'e2e-{secrets.token_hex(4)}@example.com'
pw = secrets.token_urlsafe(16)
acct = gia_identity.create_auth_user(email, pw)
from app import app
app.config['TESTING'] = True
try:
    with app.test_client() as c:
        r = c.post('/login', data={'email': email, 'password': pw})
        print('POST /login       ->', r.status_code, r.headers.get('Location'))
        with c.session_transaction() as s:
            print('  user_id  =', (s.get('user_id') or '')[:8], '| role =', s.get('user_role'))
        print('GET  /dashboard   ->', c.get('/dashboard').status_code)
        print('GET  /admin/users ->', c.get('/admin/users').status_code, '(302が正)')
    with app.test_client() as c2:
        bad = c2.post('/login', data={'email': email, 'password': 'wrong'})
        print('誤PWでログイン    ->', bad.status_code, '再表示' if bad.status_code == 200 else '')
        with c2.session_transaction() as s:
            print('  user_id  =', s.get('user_id'), '(Noneが正)')
finally:
    gia_identity.get_admin_client().auth.admin.delete_user(acct['id'])
    get_supabase_client().table('app_users').delete().eq('id', acct['id']).execute()
    print('検証用アカウント削除')
