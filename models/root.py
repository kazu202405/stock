from config import *
from flask import render_template
from models.common import *
from models.model import *


@app.route('/')
def index():
    """トップページ"""
    return render_template('index.html')


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


@app.route('/admin')
def admin():
    """管理画面"""
    return render_template('admin.html')
