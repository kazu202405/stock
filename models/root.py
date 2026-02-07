from config import *
from flask import render_template, redirect, request
from models.common import *
from models.model import *


@app.route('/')
def index():
    """ランディングページ"""
    return render_template('lp.html')


@app.route('/dashboard')
def dashboard():
    """分析ダッシュボード（閲覧専用）"""
    return render_template('stock.html', is_admin=False)


@app.route('/screener')
def screener():
    """好調企業ランキングページ"""
    return render_template('screener.html')


@app.route('/mypage')
def mypage():
    """マイページ"""
    return render_template('mypage.html')


@app.route('/community')
def community():
    """投資家コミュニティ"""
    return render_template('community.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    """ログインページ（仮実装: 何を入れてもログイン成功）"""
    if request.method == 'POST':
        return redirect('/dashboard')
    return render_template('login.html')


@app.route('/dashboard/admin')
def admin():
    """管理画面（編集可能）"""
    return render_template('stock.html', is_admin=True)
