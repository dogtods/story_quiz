import streamlit as st
import random
import json
import time
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# Google Sheets imports
# ---------------------------------------------------------------------------
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False

# ---------------------------------------------------------------------------
# ページ設定
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="ストーリー分岐クイズ",
    page_icon="📖",
    layout="centered",
)

# ---------------------------------------------------------------------------
# ダミーデータ (問題ID, コンテキスト, 設問, 正解, 誤答1, 誤答2, 正解解説, 誤答解説, 正解時遷移先ID, 誤答時遷移先ID)
# ---------------------------------------------------------------------------
DUMMY_NODES = {
    "start": {
        "id": "start",
        "context": "あなたは新米エンジニアです。最初のタスクが割り当てられました。",
        "question": "コードにバグを見つけました。どうしますか？",
        "correct": "原因を調査し、修正案を作成して先輩に相談する",
        "wrong1": "黙って修正してコミットする",
        "wrong2": "見なかったことにして放置する",
        "correct_explanation": "素晴らしい！報告・連絡・相談は基本ですね。",
        "wrong_explanation": "独断での修正や放置は、後で大きなトラブルになる可能性があります。",
        "next_id_correct": "success_1",
        "next_id_wrong": "fail_1"
    },
    "success_1": {
        "id": "success_1",
        "context": "先輩から「いい筋だね」と褒められました！",
        "question": "修正が完了しました。次にすべきことは？",
        "correct": "テストコードを書いて動作を確認する",
        "wrong1": "すぐにマージして本番環境に反映する",
        "wrong2": "コーヒーを飲みに行く",
        "correct_explanation": "品質確保のためにテストは不可欠です。",
        "wrong_explanation": "テストなしでのリリースは非常に危険です。",
        "next_id_correct": "goal",
        "next_id_wrong": "fail_2"
    },
    "fail_1": {
        "id": "fail_1",
        "context": "後日、あなたの修正が原因でシステムがダウンしてしまいました...",
        "question": "どう対応しますか？",
        "correct": "すぐにチームに報告し、状況を説明する",
        "wrong1": "自分のせいではないと主張する",
        "wrong2": "こっそり元に戻そうとする",
        "correct_explanation": "ミスを認めて迅速に共有することが、被害を最小限に抑える鍵です。",
        "wrong_explanation": "隠蔽や責任転嫁は信頼を失い、復旧を遅らせます。",
        "next_id_correct": "start",
        "next_id_wrong": "game_over"
    },
    "fail_2": {
        "id": "fail_2",
        "context": "本番環境でエラーが発生しました。テスト不足だったようです。",
        "question": "再発防止策として適切なのは？",
        "correct": "CI/CDを導入し、自動テストを必須にする",
        "wrong1": "「次から気をつける」と心に誓う",
        "wrong2": "担当者を交代する",
        "correct_explanation": "仕組みで解決するのがエンジニアの役割です。",
        "wrong_explanation": "精神論や個人の責任に帰結させても、再発は防げません。",
        "next_id_correct": "goal",
        "next_id_wrong": "game_over"
    },
    "goal": {
        "id": "goal",
        "context": "おめでとうございます！プロジェクトは大成功です。",
        "question": "最後のメッセージ",
        "correct": "最初から遊ぶ",
        "wrong1": "なし",
        "wrong2": "なし",
        "correct_explanation": "これまでの経験はあなたの糧になります。",
        "wrong_explanation": "-",
        "next_id_correct": "start",
        "next_id_wrong": "start"
    },
    "game_over": {
        "id": "game_over",
        "context": "残念ながら、あなたのキャリアはここで終わってしまいました...",
        "question": "再挑戦しますか？",
        "correct": "はい",
        "wrong1": "いいえ",
        "wrong2": "なし",
        "correct_explanation": "失敗から学び、次はもっとうまくやりましょう。",
        "wrong_explanation": "-",
        "next_id_correct": "start",
        "next_id_wrong": "start"
    }
}

# ---------------------------------------------------------------------------
# Google Sheets 読み込み
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Google Sheets 読み込み
# ---------------------------------------------------------------------------
def load_nodes_from_sheets(url: str):
    """Google Sheetsから問題データを読み込み、ノード辞書に変換する。"""
    if not url or not GSPREAD_AVAILABLE:
        return DUMMY_NODES
    
    try:
        scope = [
            'https://www.googleapis.com/auth/spreadsheets.readonly',
            'https://www.googleapis.com/auth/drive.readonly'
        ]
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)
        sh = client.open_by_url(url)
        worksheet = sh.sheet1  # 1枚目のシートを想定
        rows = worksheet.get_all_values()
        
        if not rows or len(rows) < 2:
            return DUMMY_NODES
            
        nodes = {}
        # ヘッダーを飛ばしてループ [ID, コンテキスト, 設問, 正解, 誤答1, 誤答2, 正解解説, 誤答1解説, 誤答2解説, 正解時遷移先ID, 誤答時遷移先ID]
        for r in rows[1:]:
            if len(r) < 11 or not r[0].strip():
                continue
            
            # IDを小文字化・トリミングして統一
            node_id = r[0].strip().lower()
            nodes[node_id] = {
                "id": node_id,
                "context": r[1].strip(),
                "question": r[2].strip(),
                "correct": r[3].strip(),
                "wrong1": r[4].strip(),
                "wrong2": r[5].strip(),
                "correct_explanation": r[6].strip(),
                "wrong1_explanation": r[7].strip(),
                "wrong2_explanation": r[8].strip(),
                "next_id_correct": r[9].strip().lower(), # 遷移先も小文字化
                "next_id_wrong": r[10].strip().lower()    # 遷移先も小文字化
            }
        
        if nodes:
            st.success(f"シートから {len(nodes)} 件のデータを読み込みました。")
            return nodes
        else:
            return DUMMY_NODES
    except Exception as e:
        st.error(f"シートの読み込みに失敗しました。ダミーデータを使用します: {e}")
        return DUMMY_NODES


# ---------------------------------------------------------------------------
# Gemini AI 連携
# ---------------------------------------------------------------------------
import requests as _requests

def _call_gemini(prompt: str, api_key: str) -> str:
    """Gemini REST APIを共通呼び出し関数（検索連携あり・リトライ処理付き）。"""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/gemini-flash-lite-latest:generateContent?key={api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"googleSearch": {}}]
    }
    
    max_retries = 3
    for i in range(max_retries):
        try:
            resp = _requests.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except _requests.exceptions.HTTPError as e:
            status_code = e.response.status_code
            if status_code in [429, 500, 503, 504] and i < max_retries - 1:
                # 指数バックオフ (2, 4, 8秒)
                wait_time = (2 ** (i + 1))
                time.sleep(wait_time)
                continue
            
            # エラーメッセージの日本語化
            if status_code == 429:
                raise Exception("AIの利用制限に達しました。少し時間を置いてから再度お試しください。")
            elif status_code == 503:
                raise Exception("AIサーバーが一時的に混み合っています。数分後に再度お試しください。")
            elif status_code in [500, 504]:
                raise Exception("AIサーバーでエラーが発生しました。時間を置いて再度お試しください。")
            else:
                raise Exception(f"通信エラーが発生しました (Status: {status_code})")
        except _requests.exceptions.RequestException as e:
            if i < max_retries - 1:
                time.sleep(2)
                continue
            raise Exception(f"ネットワーク接続エラーが発生しました: {e}")
    
    raise Exception("AIからの応答が得られませんでした。")

# ---------------------------------------------------------------------------
# サイドバー設定
# ---------------------------------------------------------------------------
if "url_dict" not in st.session_state:
    # 初期リストの構築
    initial_decks = {}
    default_url = st.secrets.get("spreadsheet_url", "")
    if default_url:
        initial_decks["メイン"] = default_url
    
    if "decks" in st.secrets:
        for name, info in st.secrets["decks"].items():
            if "url" in info:
                initial_decks[name] = info["url"]
    
    # もし一つも設定がない場合は空にならないようにダミーまたは警告用の誘導を入れる
    if not initial_decks:
        initial_decks["(未設定)"] = ""
        
    st.session_state.url_dict = initial_decks

with st.sidebar:
    st.title("⚙️ 設定")
    
    # 1. 問題集（デッキ）管理
    st.caption("問題集の管理")
    # URL登録フォーム（expanderに隠す）
    with st.expander("➕ 新しい問題集を登録"):
        new_name = st.text_input("名前（例: 新プロジェクト）", key="new_deck_name")
        new_url = st.text_input("スプレッドシートのURL", key="new_deck_url")
        if st.button("登録する"):
            if new_name and new_url:
                st.session_state.url_dict[new_name] = new_url
                # 「(未設定)」があれば削除
                if "(未設定)" in st.session_state.url_dict:
                    del st.session_state.url_dict["(未設定)"]
                st.success(f"「{new_name}」を登録しました")
                st.rerun()
            else:
                st.warning("名前とURLの両方を入力してください")

    # プルダウン選択
    options_keys = list(st.session_state.url_dict.keys())
    
    if not options_keys:
        st.error("問題集が登録されていません。上のフォームから登録してください。")
        st.stop()
        
    selected_deck_name = st.selectbox("問題集を選択", options_keys, key="deck_selector_sidebar")
    selected_deck_url = st.session_state.url_dict.get(selected_deck_name, "")
    
    # 再読み込みボタン
    if selected_deck_url:
        if st.button("🔄 データを再読み込み", use_container_width=True, key="reload_data_btn"):
            st.session_state.nodes = load_nodes_from_sheets(selected_deck_url)
            st.success("最新データを読み込みました")
            time.sleep(1)
            st.rerun()

    st.divider()
    st.caption("アプリ情報")
    st.info("ストーリー分岐型クイズアプリ v1.2")

# URLが変更された場合の処理
if "current_url" not in st.session_state or st.session_state.current_url != selected_deck_url:
    st.session_state.current_url = selected_deck_url
    st.session_state.nodes = load_nodes_from_sheets(selected_deck_url)
    # 状態リセット
    st.session_state.current_node_id = "start"
    st.session_state.history_path = ["start"]
    st.session_state.view_state = "question"
    st.session_state.quiz_answered_correct = False
    st.session_state.ai_chat_history = []

# ---------------------------------------------------------------------------
# セッション状態の初期化
# ---------------------------------------------------------------------------
if "nodes" not in st.session_state:
    st.session_state.nodes = load_nodes_from_sheets(selected_deck_url)
if "current_node_id" not in st.session_state:
    st.session_state.current_node_id = "start"
if "history_path" not in st.session_state:
    st.session_state.history_path = ["start"]
if "view_state" not in st.session_state:
    st.session_state.view_state = "question"
if "quiz_answered_correct" not in st.session_state:
    st.session_state.quiz_answered_correct = False
if "pending_history" not in st.session_state:
    st.session_state.pending_history = []
if "ai_chat_history" not in st.session_state:
    st.session_state.ai_chat_history = []

# ---------------------------------------------------------------------------
# Google Sheets 連携
# ---------------------------------------------------------------------------
def add_history_record(word: str, correct: bool):
    jst = timezone(timedelta(hours=9))
    timestamp = datetime.now(jst).isoformat()
    record = {
        "word": word,
        "correct": correct,
        "timestamp": timestamp,
    }
    st.session_state.pending_history.append(record)
    if len(st.session_state.pending_history) >= 5:
        flush_history_to_sheets()

def flush_history_to_sheets():
    try:
        pending = st.session_state.pending_history
        if not pending: return
        url = st.session_state.get("current_url")
        if not url or not GSPREAD_AVAILABLE: return
        scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)
        sh = client.open_by_url(url)
        try:
            worksheet = sh.worksheet("History")
        except gspread.WorksheetNotFound:
            worksheet = sh.add_worksheet(title="History", rows=1000, cols=3)
            worksheet.append_row(["Timestamp", "Word", "Correct"])
        rows_to_add = [[r["timestamp"], r["word"], "Correct" if r["correct"] else "Wrong"] for r in pending]
        worksheet.append_rows(rows_to_add)
        st.session_state.pending_history = []
        st.toast("学習履歴を保存しました！", icon="✅")
    except Exception as e:
        st.error(f"保存失敗: {e}")

# ---------------------------------------------------------------------------
# UI コンポーネント
# ---------------------------------------------------------------------------
def render_header():
    st.title("📖 ストーリー分岐クイズ")
    path_str = " ➔ ".join([f"`{id}`" for id in st.session_state.history_path])
    st.markdown(f"**現在のパス:** {path_str}")
    st.divider()

def handle_answer(is_correct, option_idx=0):
    st.session_state.quiz_answered_correct = is_correct
    st.session_state.last_option_idx = option_idx
    st.session_state.view_state = "explanation"
    st.session_state.ai_chat_history = [] # チャットリセット
    current_node = st.session_state.nodes[st.session_state.current_node_id]
    add_history_record(f"Node:{current_node['id']}", is_correct)
    st.rerun()

def next_question():
    current_node = st.session_state.nodes[st.session_state.current_node_id]
    next_id = current_node["next_id_correct"] if st.session_state.quiz_answered_correct else current_node["next_id_wrong"]
    st.session_state.current_node_id = next_id
    st.session_state.history_path.append(next_id)
    st.session_state.view_state = "question"
    flush_history_to_sheets()
    st.rerun()

# ---------------------------------------------------------------------------
# メイン表示ロジック
# ---------------------------------------------------------------------------
def main():
    render_header()
    node_id = st.session_state.current_node_id
    if node_id not in st.session_state.nodes:
        st.error(f"Node ID '{node_id}' が見つかりません。")
        st.warning(f"読み込まれたID一覧: {list(st.session_state.nodes.keys())}")
        if st.button("最初に戻る"):
            st.session_state.current_node_id = "start"
            st.session_state.history_path = ["start"]
            st.session_state.view_state = "question"
            st.rerun()
        return

    if node_id == "End":
        st.balloons()
        st.success("🎉 全問クリア！おめでとうございます！")
        if st.button("最初からやり直す", use_container_width=True):
            st.session_state.current_node_id = "start"
            st.session_state.history_path = ["start"]
            st.session_state.view_state = "question"
            st.session_state.ai_chat_history = []
            st.rerun()
        return

    node = st.session_state.nodes[node_id]

    if st.session_state.view_state == "question":
        st.subheader(f"📍 {node['context']}")
        st.info(node["question"])
        options = [(node["correct"], True, 0), (node["wrong1"], False, 1), (node["wrong2"], False, 2)]
        options = [opt for opt in options if opt[0] != "なし"]
        for text, is_correct, idx in options:
            if st.button(text, key=f"btn_{idx}", use_container_width=True):
                handle_answer(is_correct, idx)

    elif st.session_state.view_state == "explanation":
        if st.session_state.quiz_answered_correct:
            st.success("✨ 正解！")
            st.write(node["correct_explanation"])
        else:
            st.error("❌ 不正解...")
            if st.session_state.get("last_option_idx") == 1:
                st.write(node.get("wrong1_explanation", "解説がありません"))
            else:
                st.write(node.get("wrong2_explanation", "解説がありません"))
        
        # AIチャット機能
        st.divider()
        st.subheader("🤖 AIに質問する")
        
        # 履歴表示
        for chat in st.session_state.ai_chat_history:
            with st.chat_message(chat["role"]):
                st.write(chat["content"])
        
        if chat_input := st.chat_input("この問題についてもっと詳しく聞く..."):
            # APIキーは secrets から取得
            api_key_val = st.secrets.get("gemini_api_key", "")
            
            if not api_key_val:
                st.warning("secrets.toml で gemini_api_key を設定してください")
            else:
                # コンテキスト構築
                chosen_text = node["correct"] if st.session_state.quiz_answered_correct else (node["wrong1"] if st.session_state.get("last_option_idx") == 1 else node["wrong2"])
                context_prompt = (
                    f"あなたは教育アシスタントです。以下の問題についてユーザーと対話しています。\n"
                    f"【状況】{node['context']}\n"
                    f"【設問】{node['question']}\n"
                    f"【正解】{node['correct']}\n"
                    f"【ユーザーの選択】{chosen_text}\n"
                    f"【正解の解説】{node['correct_explanation']}\n\n"
                    f"ユーザーからの質問: {chat_input}\n"
                    f"上記コンテキストを踏まえ、わかりやすく回答してください。"
                )
                
                st.session_state.ai_chat_history.append({"role": "user", "content": chat_input})
                with st.chat_message("user"):
                    st.write(chat_input)
                
                with st.spinner("AI思考中..."):
                    try:
                        response = _call_gemini(context_prompt, api_key_val)
                        st.session_state.ai_chat_history.append({"role": "assistant", "content": response})
                        with st.chat_message("assistant"):
                            st.write(response)
                    except Exception as e:
                        st.error(str(e))

        st.divider()
        if st.button("次の問題へ ➡️", type="primary", use_container_width=True):
            next_question()

if __name__ == "__main__":
    main()
