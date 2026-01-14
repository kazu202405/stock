from config import *
from flask import render_template, request, jsonify
from flask_login import login_required, current_user
from models.common import *
from models.model import *


@login_required
@app.route('/chatbot', methods=['GET', 'POST'])
def chatbot():
    """チャットボットページ"""
    # データベースからメッセージを取得してテンプレートに渡す
    messages = []
    for message in Message.query.order_by(Message.timestamp).filter_by(user_id=current_user.id).all():
        message.message = format_message(message.message)
        messages.append(message)
    return render_template('chatbot.html', messages=messages)


@login_required
@app.route('/chat', methods=['POST'])
def chat():
    user_input = request.form.get('message')

    # 過去の会話をすべて取得し、適切な形式でプロンプトを作成
    before_msgs = Message.query.filter_by(user_id=current_user.id).order_by(Message.timestamp).all()

    # 過去の会話をプロンプト形式に変換
    conversation_history = ""
    for msg in before_msgs:
        if msg.user_type == 'user':
            conversation_history += f"User: {msg.message}\n"
        else:
            conversation_history += f"Bot: {msg.message}\n"

    # 現在のユーザー入力もプロンプトに追加
    conversation_history += f"User: {user_input}\n"

    # ユーザーメッセージをデータベースに保存
    user_msg = Message(user_id=current_user.id, user_type='user', message=user_input)
    db.session.add(user_msg)
    db.session.commit()

    # 過去の会話履歴とユーザー入力をAPIに送信し、応答を生成
    response = send_chat_with_dify(query=conversation_history, conversation_id="", user="abc-123")

    # Botの応答をデータベースに保存
    bot_msg = Message(user_id=current_user.id, user_type='bot', message=response["answer"])
    db.session.add(bot_msg)
    db.session.commit()
    
    answer = format_message(response["answer"])

    return jsonify({'response': answer})

