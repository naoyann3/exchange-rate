from flask import Flask
import threading
import discord
from discord.ext import commands
import re
import os
import json
from datetime import datetime

# Flask設定（ヘルスチェック用）
app = Flask(__name__)

@app.route('/health')
def health():
    print(f"Debug: Health check requested: {datetime.now()}", flush=True)
    return 'OK', 200

# Discordボット設定
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# 許可チャンネル
ALLOWED_CHANNEL_IDS = [
    1392837025354219571,  # 為替ボットテストチャンネル
]

# 為替ボットテストチャンネル（技術的フィードバック用）
OPERATIONS_CHANNEL_ID = 1392837025354219571

PROCESSED_MESSAGE_IDS_FILE = "processed_message_ids.json"
PROCESSED_MESSAGE_IDS = set()

try:
    with open(PROCESSED_MESSAGE_IDS_FILE, "r") as f:
        PROCESSED_MESSAGE_IDS.update(set(json.load(f)))
except (FileNotFoundError, json.JSONDecodeError):
    pass

def save_processed_message_ids(message_ids):
    try:
        with open(PROCESSED_MESSAGE_IDS_FILE, "w") as f:
            json.dump(list(message_ids), f)
    except Exception as e:
        print(f"Debug: Error saving processed IDs: {e}", flush=True)
        bot.loop.create_task(notify_error(f"Error saving processed message IDs: {e}", error_type="file_error"))

async def notify_error(error_message, error_type="unknown"):
    channel = bot.get_channel(OPERATIONS_CHANNEL_ID)
    print(f"Debug: Operations channel: {channel}", flush=True)
    if channel:
        tech_message = (
            f"【為替ボット：技術的お知らせ】\n"
            f"エラーが発生しました（タイプ：{error_type}）。\n"
            f"詳細：{error_message}\n"
            f"ボットはデフォルトレート（1ドル=150円）で動作中です。運営にて対応中。"
        )
        await channel.send(tech_message)
    else:
        print(f"Debug: Operations channel not found: {OPERATIONS_CHANNEL_ID}", flush=True)
    print(f"Debug: Error details: {error_message}", flush=True)

def get_user_rate(content):
    """メッセージ冒頭の為替レートを取得"""
    lines = content.strip().split('\n')
    if lines:
        try:
            rate = float(lines[0])
            if rate <= 0:
                raise ValueError("Rate must be positive")
            return rate, lines[1:]  # レート以降の行を返す
        except ValueError:
            print(f"Debug: Invalid rate format in message: {lines[0]}", flush=True)
            bot.loop.create_task(notify_error(f"Invalid rate format: {lines[0]}", error_type="invalid_rate"))
    return None, lines  # レートが無効または未入力の場合

@bot.event
async def on_ready():
    print(f"{bot.user} が起動しました！(ユーザーレート版)", flush=True)

@bot.event
async def on_message(message):
    if message.author == bot.user or message.id in PROCESSED_MESSAGE_IDS:
        print(f"Debug: Skipping processed message ID: {message.id}", flush=True)
        return
    PROCESSED_MESSAGE_IDS.add(message.id)
    save_processed_message_ids(PROCESSED_MESSAGE_IDS)

    if message.channel.id not in ALLOWED_CHANNEL_IDS:
        print(f"Debug: Skipped message in channel {message.channel.id} ({message.channel.name}), ID: {message.id}, Content: {message.content[:100]}...", flush=True)
        await bot.process_commands(message)
        return

    content = message.content
    dollar_pattern = r"(\d+)ドル|\$(\d+(?:,\d{3})*(?:\.\d+)?)"  # $100,000対応
    cme_pattern = r"CME窓\s*黄丸\s*(\d{3,})(?:\s*ドル)?"  # スペースを任意に

    print(f"Debug: Processing message in channel {message.channel.id} ({message.channel.name}), ID: {message.id}", flush=True)
    print(f"Debug: Received message: {content[:100]}...", flush=True)

    # ユーザーが指定した為替レートを取得
    rate, content_lines = get_user_rate(content)
    if rate is None:
        rate = 150.00  # デフォルトレート
        print(f"Debug: Using default rate: {rate}", flush=True)
    else:
        print(f"Debug: Using user-provided rate: {rate}", flush=True)

    new_content = '\n'.join(content_lines).replace("@everyone", "").strip()
    modified = False
    avg_price_pos = new_content.find("平均取得単価")
    first_dollar = True

    def replace_dollar(match):
        nonlocal modified, first_dollar
        amount_str = match.group(1) or match.group(2)  # ドル or $形式
        print(f"Debug: Found dollar amount: {amount_str}", flush=True)
        try:
            amount_float = float(amount_str.replace(",", ""))
            result = int(amount_float * rate)
            amount_formatted = "{:,}".format(int(amount_float))
            result_formatted = "{:,}".format(result)
            modified = True
            base_output = f"{result_formatted}円\n{amount_formatted}ドル"
            if first_dollar:
                first_dollar = False
                return f"{base_output}\n(レート: 1ドル = {rate:.2f}円で計算)"
            return base_output
        except ValueError as e:
            print(f"Debug: Invalid amount {amount_str}: {e}", flush=True)
            return match.group(0)

    new_content = re.sub(dollar_pattern, replace_dollar, new_content)
    if modified:
        print("Debug: Dollar amounts replaced", flush=True)

    # CMEマッチのデバッグ
    cme_matches = re.findall(cme_pattern, new_content)
    print(f"Debug: CME matches found: {cme_matches}", flush=True)

    def replace_cme(match):
        nonlocal modified
        amount_str = match.group(1)
        print(f"Debug: Found CME amount: {amount_str} in pattern match", flush=True)
        try:
            amount_float = float(amount_str)
            result = int(amount_float * rate)
            amount_formatted = "{:,}".format(int(amount_float))
            result_formatted = "{:,}".format(result)
            modified = True
            print(f"Debug: Converted CME amount {amount_str} to {result_formatted}円", flush=True)
            return f"CME窓 黄丸{result_formatted}円\n{amount_formatted}ドル"
        except ValueError as e:
            print(f"Debug: Invalid CME amount {amount_str}: {e}", flush=True)
            return match.group(0)

    new_content = re.sub(cme_pattern, replace_cme, new_content)

    if not modified:
        print("Debug: No modifications made, skipping send", flush=True)
        if ("＄" in content or "$" in content and not re.search(dollar_pattern, content)):
            print(f"Debug: Skipped message ID: {message.id}, Reason: Invalid dollar format, Content: {content[:100]}...", flush=True)
        await bot.process_commands(message)
        return

    new_content = new_content.replace("平均取得単価  ", "平均取得単価　")
    new_content = new_content.replace("平均取得単価   ", "平均取得単価　")

    final_content = new_content
    print(f"Debug: Sending message in channel {message.channel.id} ({message.channel.name}): {final_content[:100]}...", flush=True)
    await message.channel.send(final_content)

    await bot.process_commands(message)

if __name__ == '__main__':
    # Flaskを別スレッドで実行
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))).start()
    bot.run(os.getenv("YOUR_BOT_TOKEN"))
