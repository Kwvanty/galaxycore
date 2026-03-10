import re
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from mcrcon import MCRcon
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'super-secret-key-123' 

# --- Настройка Базы Данных ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nickname = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

# --- НОВАЯ ТАБЛИЦА ПОКУПОК ---
class Purchase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nickname = db.Column(db.String(80), nullable=False)
    item_name = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.String(20))
    price = db.Column(db.Float)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default="Completed")

with app.app_context():
    db.create_all()

# --- ДАННЫЕ RCON ---
RCON_HOST = '77.42.49.25' 
RCON_PASS = 'JxDSNzMFUe'
RCON_PORT = 25755

def run_minecraft_command(command):
    try:
        with MCRcon(RCON_HOST, RCON_PASS, port=RCON_PORT, timeout=10) as mcr:
            return mcr.command(command)
    except Exception as e:
        print(f"❌ RCON Error: {e}")
        return None

# --- ПРОФИЛЬ ИГРОКА ---

@app.route("/profile")
def profile():
    if 'nickname' not in session:
        return redirect(url_for('mainstorage'))
    return render_template("profile.html")

@app.route("/get_profile_data")
def get_profile_data():
    if 'nickname' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    
    nick = session['nickname']
    money_raw = run_minecraft_command(f"papi parse {nick} %vault_eco_balance%") 
    kills_raw = run_minecraft_command(f"papi parse {nick} %statistic_player_kills%")
    planets_raw = run_minecraft_command(f"p look {nick}")
    
    def clean_val(val, is_planets=False):
        if not val or "Error" in val: return "0"
        clean = re.sub(r'§[0-9a-fk-or]', '', val)
        if is_planets:
            match = re.search(r'has ([\d,]+) Points', clean)
            if match: return match.group(1).replace(',', '') 
        digits = "".join(filter(str.isdigit, clean))
        return digits if digits else "0"

    return jsonify({
        "status": "success",
        "nickname": nick,
        "balance": clean_val(money_raw),
        "kills": clean_val(kills_raw),
        "planets": clean_val(planets_raw, is_planets=True),
    })

# --- АДМИН-ФУНКЦИИ ---

@app.route('/admin/get-purchases')
def get_purchases():
    if 'nickname' not in session: return jsonify([]), 403
    
    purchases = Purchase.query.order_by(Purchase.date.desc()).all()
    output = []
    for p in purchases:
        output.append({
            "nickname": p.nickname,
            "item": p.item_name,
            "amount": p.amount,
            "price": p.price,
            "date": p.date.strftime("%d.%m.%Y %H:%M")
        })
    return jsonify(output)

@app.route('/admin/get-users')
def get_users():
    if 'nickname' not in session: return jsonify({"status": "error"}), 403
    users = User.query.all()
    return jsonify([{"id": u.id, "nickname": u.nickname, "is_admin": u.is_admin} for u in users])

@app.route('/admin/execute-command', methods=['POST'])
def admin_execute():
    if 'nickname' not in session: return jsonify({"status": "error", "message": "Доступ запрещен"}), 403
    data = request.json
    cmd_type = data.get('command')
    target_nick = data.get('nickname')

    if target_nick == "SERVER_CONSOLE":
        mc_command = cmd_type
    else:
        if cmd_type == "/op": mc_command = f"op {target_nick}"
        elif cmd_type == "/deop": mc_command = f"deop {target_nick}"
        elif cmd_type == "/ban": mc_command = f"ban {target_nick} Нарушение правил"
        elif cmd_type == "/unban": mc_command = f"pardon {target_nick}"
        elif cmd_type == "/kick": mc_command = f"kick {target_nick}"
        else: mc_command = f"{cmd_type.replace('/', '')} {target_nick}"

    response = run_minecraft_command(mc_command)
    if response is not None:
        if target_nick != "SERVER_CONSOLE":
            run_minecraft_command(f"say §c[Admin] §fДействие §6{cmd_type} §fнад §e{target_nick}")
        return jsonify({"status": "success", "server_response": response if response else "Done"})
    return jsonify({"status": "error", "message": "Ошибка RCON"}), 500

@app.route("/")
def index(): return render_template("index.html")

def clean_minecraft_styles(text):
    if not text: return ""
    return re.sub(r'§[0-9a-fk-or]', '', text)

@app.route('/admin/get-all-active-players')
def get_active_players():
    if 'nickname' not in session: return jsonify([]), 403
    db_users = User.query.all()
    all_players = {u.nickname: {"id": u.id, "status": "Offline"} for u in db_users}
    raw_response = run_minecraft_command("list")
    if raw_response:
        clean_response = clean_minecraft_styles(raw_response)
        players_part = clean_response.split(":", 1)[1].strip() if ":" in clean_response else clean_response
        raw_words = re.split(r'[,\s]+', players_part)
        garbage = ['default', 'admin', 'player', 'owner', 'moder', 'helper', 'online']
        for word in raw_words:
            clean_word = word.strip().replace(':', '').replace('*', '')
            if clean_word and clean_word.lower() not in garbage and len(clean_word) >= 3:
                if clean_word in all_players: all_players[clean_word]["status"] = "ONLINE"
                else: all_players[clean_word] = {"id": "--", "status": "ONLINE (Guest)"}
    return jsonify([{"nickname": n, "id": v["id"], "status": v["status"]} for n, v in all_players.items()])

@app.route('/admin-panel')
def admin_panel():
    if 'nickname' not in session: return redirect(url_for('admin_login'))
    return render_template('adminpanel.html')

@app.route('/admin-login')
def admin_login(): return render_template('logadmin.html')

@app.route('/register')
def register_page(): return render_template('register.html')

@app.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    nickname, email, password = data.get('nickname'), data.get('email', '').lower(), data.get('password')
    if User.query.filter_by(email=email).first() or User.query.filter_by(nickname=nickname).first():
        return jsonify({"status": "error", "message": "Пользователь уже существует"}), 400
    db.session.add(User(nickname=nickname, email=email, password=password))
    db.session.commit()
    return jsonify({"status": "success", "message": "Регистрация успешна"})

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET": return render_template('login.html')
    data = request.get_json()
    user = User.query.filter_by(email=data.get('email', '').lower()).first()
    if user and user.password == data.get('password'):
        session['user_id'], session['nickname'] = user.id, user.nickname
        return jsonify({"status": "success", "nickname": user.nickname})
    return jsonify({"status": "error", "message": "Неверный логин или пароль"}), 401

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route("/mainstorige")
def main_storage():
    if 'nickname' not in session: return redirect(url_for('login'))
    return render_template("mainstorige.html")

@app.route("/donats.html")
def donats_page(): return render_template("donats.html")

@app.route("/planets.html")
def planets_page(): return render_template("planets.html")

@app.route("/casekey.html")
def casekey_page(): return render_template("casekey.html")

@app.route("/success_buy")
def success_buy_page():
    if 'nickname' not in session: 
        return redirect(url_for('login'))
        
    item_id = request.args.get('item')   
    item_name = request.args.get('name') 
    amount = request.args.get('amount', '1') 
    price = request.args.get('price', '0') 
    
    return render_template("success_buy.html", 
                           item_name=item_name or item_id.upper(), 
                           price=price, 
                           item_id=item_id, 
                           amount=amount)

@app.route("/success_buy_case")
def success_buy_case_page():
    if 'nickname' not in session: return redirect(url_for('login'))
    item_id = request.args.get('item')   
    item_name = request.args.get('name') 
    amount = request.args.get('amount', '1') 
    price = request.args.get('price', '0') 
    
    return render_template("success_buy_case.html", 
                           item_name=item_name or item_id.upper(), 
                           price=price, item_id=item_id, amount=amount,
                           is_case=True) 

@app.route('/check-before-pay', methods=['POST'])
def check_before_pay():
    data = request.json
    nickname = data.get('nickname')
    
    # ПРОВЕРКА: Команда 'list' покажет, в сети ли игрок
    # Мы используем RCON_HOST (77.42.49.25) и RCON_PORT (25755)
    response = run_minecraft_command("list")
    
    if response:
        # Если ответ от RCON пришел, проверяем, есть ли ник в списке игроков
        if nickname.lower() in response.lower():
            return jsonify({"status": "success"})
        else:
            return jsonify({"status": "error", "message": "Зайдите на сервер, чтобы купить товар!"})
    
    # Если response равен None, значит RCON вообще не ответил
    return jsonify({"status": "error", "message": "Сервер Minecraft недоступен (RCON Error)"})
# --- ОБРАБОТКА ПОКУПКИ КЕЙСОВ ---
@app.route('/buy-case', methods=['POST'])
def buy_case_logic():
    data = request.json
    nick = data.get('nickname')
    case = data.get('item')
    amount = data.get('amount', 1)
    price = data.get('price', 0)
    cantly_to_buy = "false"

    def clean_val(val, is_planets=False):
        if not val or "Error" in val: return "0"
        clean = re.sub(r'§[0-9a-fk-or]', '', val)
        if is_planets:
            match = re.search(r'has ([\d,]+) Points', clean)
            if match: return match.group(1).replace(',', '') 
        digits = "".join(filter(str.isdigit, clean))
        return digits if digits else "0"

    case_clean = str(case).lower().strip()
    command_check = f"p look {nick}"
    planets_raw = str(run_minecraft_command(command_check))
    planets_raw = clean_val(planets_raw, is_planets=True)
    
    match = re.search(r'(\d+)\s+Points', planets_raw)
    if match:
        current_balance = int(match.group(1))
    else:
        all_numbers = re.findall(r'\d+', planets_raw)
        current_balance = int(all_numbers[-1]) if all_numbers else 0
    current_balance = int(''.join(filter(str.isdigit, planets_raw)))
    if current_balance >= int(price):
        cantly_to_buy = "true"
    else:
        cantly_to_buy = "false"
    

    command = f"dc givekey {nick} {case_clean} {amount}"
    command_take = f"p take {nick} {price}"
    announcement = f"say Игрок {nick} купил ключ от кейса: {case_clean.upper()} ({amount} шт)!"
    command_error = 'say Недостачочно Планеток для покупки кейса или произошла ошибка!'
    print(cantly_to_buy)
    print(current_balance)

    if cantly_to_buy == "true":
        run_minecraft_command(command)
        run_minecraft_command(command_take)
        run_minecraft_command(announcement)
        save_purchase_to_db(nick, case_clean, amount, price)
        return jsonify({"status": "success"})
    else:
        run_minecraft_command(command_error)
        return jsonify({"status": "error", "message": "Недостаточно планеток или ошибка!"}), 400

def save_purchase_to_db(nick, item, amount, price):
    try:
        new_p = Purchase(nickname=nick, item_name=item.upper(), amount=str(amount), price=float(price))
        db.session.add(new_p)
        db.session.commit()
    except Exception as e:
        print(f"Ошибка БД: {e}")
if __name__ == "__main__":
    app.run()
