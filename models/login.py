from config import *
from flask_login import login_user, current_user
from flask import render_template, request, redirect, url_for
from models.common import *
from flask import flash
from models.model import *


def verify_password(stored_password_hash, provided_password):
    return check_password_hash(stored_password_hash, provided_password)


def check_password(user, password):
    ok_flag = False
    if user.password_hash is None:
        return False
    try:
        ok_flag = check_password_hash(user.password_hash, password)
    except Exception as e:
        if user.password_hash == password:
            ok_flag = True
    return ok_flag


@app.route("/login", methods=['GET', 'POST'])
def login():
    """ログインページと処理"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form['account_name']
        password = request.form['password']
        # データベースからメールアドレスに一致する会社情報を検索
        users = User.query.all()
        for user in users:
            print(user.email)
        user = User.query.filter_by(email=email).first()
        # ログイン試行記録用のデータ準備
        ip_address = request.remote_addr
        user_agent = request.user_agent.string
        attempt_time = dt.now()
        
        # ユーザーが存在し、かつパスワードが一致する場合
        if user and (check_password(user, password)):
        # if user and (verify_password(user.password_hash, password)):
            login_user(user, remember=request.form.get('remember') == 'on')
            user.last_login = dt.now()
            db.session.commit()
            # ログイン成功の記録
            login_attempt = LoginAttempt(ip_address=ip_address,
                                        user_agent=user_agent, status='success', attempt_time=attempt_time, user_id=user.id)
            db.session.add(login_attempt)
            db.session.commit()
            return redirect(url_for('dashboard'))            
        
        else:
            user_id = user.id if user else None
            # ログイン失敗の記録
            login_attempt = LoginAttempt(user_id=user_id, ip_address=ip_address,
                                        user_agent=user_agent, status='failure', attempt_time=attempt_time)
            db.session.add(login_attempt)
            db.session.commit()
            flash('メールアドレスまたはパスワードが間違っています。', 'danger')
    return render_template('login.html')


@app.route('/logout', methods=['GET', 'POST'])
def logout():
    """ログアウト"""
    logout_user()
    flash('ログアウトしました。', 'success')
    # return redirect(url_for('dashboard'))
    return redirect(url_for('login'))
