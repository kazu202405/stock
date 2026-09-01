# -*- coding: utf-8 -*-
"""金融庁EDINET 公式API（v2）の呼び出しをまとめる。

⚠️ **認証エラーでも HTTP 200 が返る。** 本文の StatusCode が401になる。
   `res.status_code == 200` で判定すると、鍵が無効でも「成功」として通り、
   **「全件成功なのに中身が空」**という一番気づけない壊れ方をする。
   ∴ 呼び出し口をここ1か所にまとめて、必ず本文を見る。

⚠️ **書類は日付でしか引けない。** 「この会社の最新の有報」という引き方は無い。
   ∴ 日付を1日ずつ走査して、有価証券報告書だけを拾う。
"""

from __future__ import annotations

import os

BASE_URL = 'https://api.edinet-fsa.go.jp/api/v2'

# 相手は官庁のAPI。詰めて叩かない。
DEFAULT_SLEEP = 0.6
LIST_TIMEOUT = 60
DOC_TIMEOUT = 120


class EdinetError(RuntimeError):
    """EDINETが200以外（本文のStatusCode）を返した。"""


def subscription_key():
    """公式APIの鍵。

    ⚠️ `edb_` で始まる EDINET_API_KEY は第三者サービス「EDINET DB」のもので
       別物。取り違えると認証が通らないのに200が返る。
    """
    key = (os.getenv('EDINET_SUBSCRIPTION_KEY') or '').strip()
    if not key:
        raise EdinetError('EDINET_SUBSCRIPTION_KEY が設定されていません')
    return key


def _check(body):
    status = str((body.get('metadata') or {}).get('status')
                 or body.get('StatusCode') or '')
    if status != '200':
        raise EdinetError('EDINETが %s を返しました: %s'
                          % (status, body.get('message') or body))
    return body


def list_documents(day, key=None, timeout=LIST_TIMEOUT):
    """その日に提出された書類の一覧を返す。"""
    import requests

    key = key or subscription_key()
    res = requests.get(BASE_URL + '/documents.json',
                       params={'date': day, 'type': '2'},
                       headers={'Ocp-Apim-Subscription-Key': key}, timeout=timeout)
    res.raise_for_status()
    return _check(res.json()).get('results') or []


def annual_reports(day, key=None):
    """その日に提出された有価証券報告書を {証券コード4桁: 書類} で返す。"""
    import edinet_report

    out = {}
    for doc in list_documents(day, key):
        if doc.get('docTypeCode') != edinet_report.DOC_TYPE_ANNUAL_REPORT:
            continue
        sec = doc.get('secCode')
        if not sec or len(sec) != 5:
            continue
        out[sec[:-1]] = doc
    return out


def fetch_report(doc_id, key=None, timeout=DOC_TIMEOUT):
    """有報のCSV（ZIP）を取る。

    ⚠️ 在庫切れ・認証で落ちるときはJSONが返る。ZIPでなければ止める
       （中身が空のZIPとして扱うと、静かに0件で終わる）。
    """
    import requests

    key = key or subscription_key()
    res = requests.get('%s/documents/%s' % (BASE_URL, doc_id),
                       params={'type': '5'},
                       headers={'Ocp-Apim-Subscription-Key': key}, timeout=timeout)
    res.raise_for_status()
    if res.content[:2] != b'PK':
        raise EdinetError('ZIPではありません: %s' % res.content[:180])
    return res.content
