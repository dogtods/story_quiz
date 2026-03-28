"""ストーリー分岐クイズアプリ v1.3"""

import streamlit as st
import random
import time
from datetime import datetime, timezone, timedelta

# --- Google Sheets imports ---
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False

# --- HTTP client for Gemini API ---
import requests as _requests

# --- ページ設定 ---
st.set_page_config(page_title="ストーリー分岐クイズ", page_icon="📖", layout="centered")

# =============================================================================
# ダミーデータ
# =============================================================================
DUMMY_NODES = {
    "start": {
        "id": "start", "row_index": 0,
        "context": "あなたは新米エンジニアです。最初のタスクが割り当てられました。",
        "question": "コードにバグを見つけました。どうしますか？",
        "correct": "原因を調査し、修正案を作成して先輩に相談する",
        "wrong1": "黙って修正してコミットする",
        "wrong2": "見なかったことにして放置する",
        "correct_explanation": "素晴らしい！報告・連絡・相談は基本ですね。",
        "wrong1_explanation": "独断での修正は、後で大きなトラブルになる可能性があります。",
        "wrong2_explanation": "放置は、後で大きなトラブルになる可能性があります。",
        "next_id_correct": "goal", "next_id_wrong": "start",
        "past_question": "", "past_answer": ""
    },
    "goal": {
        "id": "goal", "row_index": 0,
        "context": "おめでとうございます！プロジェクトは大成功です。",
        "question": "最後のメッセージ", "correct": "最初から遊ぶ",
        "wrong1": "なし", "wrong2": "なし",
        "correct_explanation": "これまでの経験はあなたの糧になります。",
        "wrong1_explanation": "-", "wrong2_explanation": "-",
        "next_id_correct": "start", "next_id_wrong": "start",
        "past_question": "", "past_answer": ""
    },
}

# =============================================================================
# Google Sheets 共通認証
# =============================================================================
def _get_gspread_client(readonly: bool = True):
    """gspread クライアントを返す。認証ロジックを一箇所に集約。"""
    if not GSPREAD_AVAILABLE:
        return None
    if readonly:
        scope = [
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
    else:
        scope = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]), scopes=scope
    )
    return gspread.authorize(creds)


# =============================================================================
# データ読み込み・保存
# =============================================================================
def load_nodes_from_sheets(url: str):
    """Google Sheets から問題データを読み込む。"""
    if not url or not GSPREAD_AVAILABLE:
        return DUMMY_NODES
    try:
        client = _get_gspread_client(readonly=True)
        rows = client.open_by_url(url).sheet1.get_all_values()
        if not rows or len(rows) < 2:
            return DUMMY_NODES

        nodes = {}
        for i, r in enumerate(rows[1:], start=2):
            if len(r) < 11 or not r[0].strip():
                continue
            nid = r[0].strip().lower()
            nodes[nid] = {
                "id": nid,
                "row_index": i,
                "context":             r[1].strip()         if len(r) > 1  else "",
                "question":            r[2].strip()         if len(r) > 2  else "",
                "correct":             r[3].strip()         if len(r) > 3  else "",
                "wrong1":              r[4].strip()         if len(r) > 4  else "",
                "wrong2":              r[5].strip()         if len(r) > 5  else "",
                "correct_explanation": r[6].strip()         if len(r) > 6  else "",
                "wrong1_explanation":  r[7].strip()         if len(r) > 7  else "",
                "wrong2_explanation":  r[8].strip()         if len(r) > 8  else "",
                "next_id_correct":     r[9].strip().lower() if len(r) > 9  else "",
                "next_id_wrong":       r[10].strip().lower()if len(r) > 10 else "",
                "past_question":       r[11].strip()        if len(r) > 11 else "",
                "past_answer":         r[12].strip()        if len(r) > 12 else "",
            }
        if nodes:
            st.success(f"シートから {len(nodes)} 件のデータを読み込みました。")
            return nodes
        return DUMMY_NODES
    except Exception as e:
        st.error(f"シート読み込み失敗（ダミーデータを使用）: {e}")
        return DUMMY_NODES


def save_ai_chat_to_sheets(url: str, row_index: int, question: str, answer: str):
    """AI 質問・回答を L列/M列 に追記保存する。"""
    if not url or not GSPREAD_AVAILABLE or row_index == 0:
        return
    try:
        ws = _get_gspread_client(readonly=False).open_by_url(url).sheet1
        old_q = ws.cell(row_index, 12).value or ""
        old_a = ws.cell(row_index, 13).value or ""
        sep = "\n---\n"
        ws.update_cell(row_index, 12, f"{old_q}{sep}{question}" if old_q else question)
        ws.update_cell(row_index, 13, f"{old_a}{sep}{answer}" if old_a else answer)
    except Exception as e:
        st.error(f"履歴保存失敗: {e}")


def add_history_record(word: str, correct: bool):
    """学習履歴をバッファに追加し、5件溜まったらフラッシュ。"""
    jst = timezone(timedelta(hours=9))
    st.session_state.pending_history.append({
        "word": word,
        "correct": correct,
        "timestamp": datetime.now(jst).isoformat(),
    })
    if len(st.session_state.pending_history) >= 5:
        flush_history_to_sheets()


def flush_history_to_sheets():
    """バッファ内の学習履歴を History シートに書き出す。"""
    pending = st.session_state.pending_history
    if not pending:
        return
    url = st.session_state.get("current_url")
    if not url or not GSPREAD_AVAILABLE:
        return
    try:
        client = _get_gspread_client(readonly=False)
        sh = client.open_by_url(url)
        try:
            ws = sh.worksheet("History")
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title="History", rows=1000, cols=3)
            ws.append_row(["Timestamp", "Word", "Correct"])
        ws.append_rows([
            [r["timestamp"], r["word"], "Correct" if r["correct"] else "Wrong"]
            for r in pending
        ])
        st.session_state.pending_history = []
        st.toast("学習履歴を保存しました！", icon="✅")
    except Exception as e:
        st.error(f"保存失敗: {e}")


# =============================================================================
# Gemini AI（トークン節約仕様）
# =============================================================================
def _call_gemini(prompt: str, api_key: str) -> str:
    """Gemini REST API 呼び出し（リトライ付き）。"""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/gemini-flash-lite-latest:generateContent?key={api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": st.session_state.get("ai_max_tokens", 300),
            "temperature": st.session_state.get("ai_temperature", 0.3),
        },
    }
    for i in range(3):
        try:
            resp = _requests.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        except _requests.exceptions.HTTPError as e:
            code = e.response.status_code
            if code in (429, 500, 503, 504) and i < 2:
                time.sleep(2 ** (i + 1))
                continue
            msgs = {429: "AI利用制限に達しました。", 503: "AIサーバー混雑中。",
                    500: "AIサーバーエラー。", 504: "AIサーバーエラー。"}
            raise Exception(msgs.get(code, f"通信エラー (Status: {code})"))
        except _requests.exceptions.RequestException:
            if i < 2:
                time.sleep(2)
                continue
            raise Exception("ネットワーク接続エラー。")
    raise Exception("AIから応答を得られませんでした。")


# =============================================================================
# ユーティリティ
# =============================================================================
def get_shuffled_options(node):
    """選択肢をシャッフルして返す（セッション内は固定）。"""
    key = f"options_{node['id']}"
    if key not in st.session_state:
        opts = [
            {"text": node["correct"], "is_correct": True},
            {"text": node["wrong1"],   "is_correct": False},
            {"text": node["wrong2"],   "is_correct": False},
        ]
        opts = [o for o in opts if o["text"] != "なし"]
        random.shuffle(opts)
        st.session_state[key] = opts
    return st.session_state[key]


def reset_to_start():
    """クイズ状態を初期化して start に戻す。"""
    st.session_state.current_node_id = "start"
    st.session_state.history_path = ["start"]
    st.session_state.view_state = "question"
    st.session_state.ai_chat_history = []
    st.rerun()


# =============================================================================
# サイドバー
# =============================================================================
if "url_dict" not in st.session_state:
    initial = {}
    default_url = st.secrets.get("spreadsheet_url", "")
    if default_url:
        initial["メイン"] = default_url
    if "decks" in st.secrets:
        for name, info in st.secrets["decks"].items():
            if "url" in info:
                initial[name] = info["url"]
    if not initial:
        initial["(未設定)"] = ""
    st.session_state.url_dict = initial

with st.sidebar:
    st.title("⚙️ 設定")
    st.caption("問題集の管理")

    options_keys = list(st.session_state.url_dict.keys())
    if not options_keys or options_keys == ["(未設定)"]:
        st.error("問題集が登録されていません。secrets.tomlに問題集を設定してください。")
        st.stop()

    selected_deck_name = st.selectbox("問題集を選択", options_keys, key="deck_selector_sidebar")
    selected_deck_url  = st.session_state.url_dict.get(selected_deck_name, "")

    if selected_deck_url:
        if st.button("🔄 データを再読み込み", use_container_width=True, key="reload_data_btn"):
            st.session_state.nodes = load_nodes_from_sheets(selected_deck_url)
            st.success("最新データを読み込みました")
            time.sleep(1)
            st.rerun()

    st.divider()
    st.caption("アプリ情報")
    st.info("ストーリー分岐型クイズアプリ v1.3")

    st.divider()
    with st.expander("🛠️ AI高度な設定"):
        st.slider("Temperature", 0.0, 1.0, 0.3, 0.1, key="ai_temperature", help="高いほど創造的、低いほど正確")
        st.number_input("Max Output Tokens", 100, 2048, 300, 50, key="ai_max_tokens", help="AI回答の最大文字数")

# --- URL 変更検知 ---
if "current_url" not in st.session_state or st.session_state.current_url != selected_deck_url:
    st.session_state.current_url = selected_deck_url
    st.session_state.nodes = load_nodes_from_sheets(selected_deck_url)
    st.session_state.current_node_id = "start"
    st.session_state.history_path = ["start"]
    st.session_state.view_state = "question"
    st.session_state.quiz_answered_correct = False
    st.session_state.ai_chat_history = []

# --- セッション初期化 ---
_defaults = {
    "nodes": lambda: load_nodes_from_sheets(selected_deck_url),
    "current_node_id": "start",
    "history_path": ["start"],
    "view_state": "question",
    "quiz_answered_correct": False,
    "pending_history": [],
    "ai_chat_history": [],
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v() if callable(v) else v


# =============================================================================
# メインロジック
# =============================================================================
def handle_answer(is_correct: bool, selected_text: str):
    """回答を処理する。"""
    st.session_state.quiz_answered_correct = is_correct
    st.session_state.selected_option_text = selected_text
    st.session_state.view_state = "explanation"
    st.session_state.ai_chat_history = []
    node = st.session_state.nodes[st.session_state.current_node_id]
    add_history_record(f"Node:{node['id']}", is_correct)
    st.rerun()


def next_question():
    """次の問題へ遷移する。"""
    node = st.session_state.nodes[st.session_state.current_node_id]
    nxt = node["next_id_correct"] if st.session_state.quiz_answered_correct else node["next_id_wrong"]
    st.session_state.current_node_id = nxt
    st.session_state.history_path.append(nxt)
    st.session_state.view_state = "question"
    flush_history_to_sheets()
    st.rerun()


def render_header():
    path_str = " ➔ ".join([f"`{p}`" for p in st.session_state.history_path])
    st.caption(f"現在のパス: {path_str}")
    st.divider()


def main():
    render_header()
    node_id = st.session_state.current_node_id

    # --- ノード未検出 ---
    if node_id not in st.session_state.nodes:
        st.error(f"Node ID '{node_id}' が見つかりません。")
        if st.button("最初に戻る"):
            reset_to_start()
        return

    # --- End 到達 ---
    if node_id == "End":
        st.balloons()
        st.success("🎉 全問クリア！おめでとうございます！")
        if st.button("最初からやり直す", use_container_width=True):
            reset_to_start()
        return

    node = st.session_state.nodes[node_id]

    # ========== 出題画面 ==========
    if st.session_state.view_state == "question":
        st.write(f"**📍 {node['context']}**")
        st.info(node["question"])
        for opt in get_shuffled_options(node):
            if st.button(opt["text"], key=f"btn_{opt['text']}", use_container_width=True):
                handle_answer(opt["is_correct"], opt["text"])

    # ========== 解説画面 ==========
    elif st.session_state.view_state == "explanation":
        # 設問と正解を再表示
        st.write(f"**📍 {node['context']}**")
        st.info(f"**設問:** {node['question']}\n\n**正解:** {node['correct']}")
        st.divider()

        if st.session_state.quiz_answered_correct:
            st.success("✨ 正解！")
            st.write(node["correct_explanation"])
        else:
            st.error("❌ 不正解...")
            sel = st.session_state.get("selected_option_text", "")
            if sel == node.get("wrong1"):
                st.write(node.get("wrong1_explanation", "解説がありません"))
            else:
                st.write(node.get("wrong2_explanation", "解説がありません"))

        # AI チャット
        st.divider()
        st.subheader("🤖 AIに質問する")

        for chat in st.session_state.ai_chat_history:
            with st.chat_message(chat["role"]):
                st.write(chat["content"])

        if user_q := st.chat_input("この問題についてもっと詳しく聞く..."):
            api_key = st.secrets.get("gemini_api_key", "")
            if not api_key:
                st.warning("secrets.toml で gemini_api_key を設定してください")
            else:
                # トークン節約型プロンプト（必要最小限のコンテキスト）
                prompt = (
                    f"設問:{node['question']}\n正解:{node['correct']}\n"
                    f"解説:{node['correct_explanation']}\n"
                    f"質問:{user_q}\n"
                    "挨拶・前置き不要。簡潔に回答。"
                )

                st.session_state.ai_chat_history.append({"role": "user", "content": user_q})
                with st.chat_message("user"):
                    st.write(user_q)

                with st.spinner("AI思考中..."):
                    try:
                        reply = _call_gemini(prompt, api_key)
                        st.session_state.ai_chat_history.append({"role": "assistant", "content": reply})
                        save_ai_chat_to_sheets(
                            st.session_state.get("current_url", ""),
                            node.get("row_index", 0),
                            user_q, reply,
                        )
                        with st.chat_message("assistant"):
                            st.write(reply)
                    except Exception as e:
                        st.error(str(e))

        # 過去の AI 履歴（複数対応） - チャット欄の下に配置
        if node.get("past_question") and node.get("past_answer"):
            st.divider()
            with st.expander("📝 過去のAIとのやり取りをすべて確認する"):
                qs = node["past_question"].split("\n---\n")
                ans = node["past_answer"].split("\n---\n")
                for idx, (pq, pa) in enumerate(zip(qs, ans), 1):
                    st.markdown(f"**対話 {idx}**")
                    st.caption(f"質問: {pq}")
                    st.write(pa)
                    st.divider()

        st.divider()
        if st.button("次の問題へ ➡️", type="primary", use_container_width=True):
            next_question()


if __name__ == "__main__":
    main()
