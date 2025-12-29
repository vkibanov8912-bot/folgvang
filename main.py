#!/usr/bin/env python3
"""
Folkvang Boss Tracker Server for Render.com
"""

from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit
from datetime import datetime, timedelta
import json
import logging
from threading import Lock
import os

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'folkvang-secret-key-2024')

# Настройка CORS для Render
socketio = SocketIO(
    app, 
    cors_allowed_origins="*",
    async_mode='gevent',
    logger=True,
    engineio_logger=True
)

# ===== ХРАНИЛИЩЕ ДАННЫХ =====
class BossStorage:
    def __init__(self):
        self.lock = Lock()
        self.bosses = self.init_bosses()
        self.kill_history = []
        
    def init_bosses(self):
        """Инициализация данных боссов"""
        bosses = {}
        for floor in range(1, 5):
            floor_key = f'floor{floor}'
            bosses[floor_key] = {
                'mage': None,      # Вёльва
                'healer': None,    # Скальд  
                'spearman': None,  # Копейщик
                'berserk': None    # Берсерк
            }
        return bosses
    
    def kill_boss(self, floor, boss_type, player):
        """Отметить убийство босса"""
        with self.lock:
            floor_key = f'floor{floor}'
            
            if floor_key in self.bosses and boss_type in self.bosses[floor_key]:
                kill_time = datetime.now()
                
                # Сохраняем убийство
                self.bosses[floor_key][boss_type] = {
                    'kill_time': kill_time.isoformat(),
                    'player': player,
                    'respawn_minutes': 120
                }
                
                # Добавляем в историю
                self.kill_history.append({
                    'floor': floor,
                    'boss': boss_type,
                    'player': player,
                    'kill_time': kill_time.isoformat(),
                    'respawn': 120
                })
                
                # Ограничиваем историю последними 100 убийствами
                if len(self.kill_history) > 100:
                    self.kill_history = self.kill_history[-100:]
                
                return True
            return False
    
    def get_boss_state(self):
        """Получить текущее состояние"""
        with self.lock:
            return self.bosses.copy()
    
    def get_recent_kills(self, hours=2):
        """Получить последние убийства"""
        with self.lock:
            cutoff = datetime.now() - timedelta(hours=hours)
            recent = []
            
            for kill in reversed(self.kill_history):
                kill_time = datetime.fromisoformat(kill['kill_time'])
                if kill_time > cutoff:
                    recent.append(kill)
                else:
                    break
            
            return recent
    
    def reset_all(self):
        """Сбросить всех боссов"""
        with self.lock:
            self.bosses = self.init_bosses()
            return True

# Создаем хранилище
storage = BossStorage()

# ===== HTTP РОУТЫ =====
@app.route('/')
def home():
    """Главная страница"""
    return jsonify({
        'status': 'online',
        'service': 'Folkvang Boss Tracker',
        'version': '1.0.0',
        'timestamp': datetime.now().isoformat(),
        'endpoints': {
            '/': 'Эта страница',
            '/api/status': 'Статус боссов',
            '/api/kills': 'Последние убийства',
            '/health': 'Проверка здоровья'
        }
    })

@app.route('/health')
def health_check():
    """Проверка здоровья для Render"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/status')
def get_status():
    """Получить статус всех боссов"""
    return jsonify(storage.get_boss_state())

@app.route('/api/kills')
def get_kills():
    """Получить последние убийства"""
    hours = request.args.get('hours', default=2, type=int)
    recent_kills = storage.get_recent_kills(hours)
    return jsonify({'kills': recent_kills, 'count': len(recent_kills)})

@app.route('/api/kill', methods=['POST'])
def report_kill():
    """Сообщить об убийстве (HTTP версия)"""
    try:
        data = request.json
        
        if not data:
            return jsonify({'error': 'No JSON data'}), 400
        
        floor = data.get('floor')
        boss = data.get('boss')
        player = data.get('player', 'Игрок')
        
        if not floor or not boss:
            return jsonify({'error': 'Missing floor or boss'}), 400
        
        if storage.kill_boss(floor, boss, player):
            # Отправляем через WebSocket
            kill_data = {
                'action': 'boss_killed',
                'floor': floor,
                'boss': boss,
                'player': player,
                'kill_time': storage.bosses[f'floor{floor}'][boss]['kill_time'],
                'respawn_minutes': 120
            }
            
            socketio.emit('boss_update', kill_data, broadcast=True)
            
            return jsonify({'success': True, 'data': kill_data})
        
        return jsonify({'error': 'Invalid floor or boss'}), 400
        
    except Exception as e:
        logger.error(f"Error in report_kill: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/reset', methods=['POST'])
def reset_all():
    """Сбросить всех боссов (админ)"""
    auth_token = request.headers.get('X-Auth-Token')
    expected_token = os.environ.get('ADMIN_TOKEN', 'admin123')
    
    if auth_token != expected_token:
        return jsonify({'error': 'Unauthorized'}), 401
    
    if storage.reset_all():
        socketio.emit('reset_all', {}, broadcast=True)
        return jsonify({'success': True})
    
    return jsonify({'error': 'Reset failed'}), 500

# ===== WEBSOCKET ОБРАБОТЧИКИ =====
@socketio.on('connect')
def handle_connect():
    """Новый клиент подключился"""
    client_id = request.sid
    logger.info(f"📱 WebSocket connected: {client_id}")
    
    # Отправляем текущее состояние
    emit('connected', {
        'message': 'Connected to Folkvang Server',
        'server_time': datetime.now().isoformat(),
        'client_id': client_id
    })
    
    # Отправляем начальное состояние
    emit('initial_state', storage.get_boss_state())

@socketio.on('disconnect')
def handle_disconnect():
    """Клиент отключился"""
    client_id = request.sid
    logger.info(f"📴 WebSocket disconnected: {client_id}")

@socketio.on('boss_kill')
def handle_boss_kill(data):
    """Обработка убийства через WebSocket"""
    try:
        floor = data.get('floor')
        boss = data.get('boss')
        player = data.get('player', 'Unknown')
        
        logger.info(f"🎯 WebSocket kill: {player} killed {boss} on floor {floor}")
        
        if storage.kill_boss(floor, boss, player):
            # Рассылаем всем
            kill_data = {
                'action': 'boss_killed',
                'floor': floor,
                'boss': boss,
                'player': player,
                'kill_time': storage.bosses[f'floor{floor}'][boss]['kill_time'],
                'respawn_minutes': 120
            }
            
            emit('boss_update', kill_data, broadcast=True, include_self=False)
            emit('kill_confirmed', {'success': True, 'data': kill_data})
        else:
            emit('kill_confirmed', {'success': False, 'error': 'Invalid data'})
            
    except Exception as e:
        logger.error(f"Error in handle_boss_kill: {e}")
        emit('kill_confirmed', {'success': False, 'error': str(e)})

@socketio.on('ping')
def handle_ping():
    """Пинг для проверки соединения"""
    emit('pong', {'timestamp': datetime.now().isoformat()})

@socketio.on('get_state')
def handle_get_state():
    """Запрос текущего состояния"""
    emit('state_update', storage.get_boss_state())

# ===== ЗАПУСК СЕРВЕРА =====
if __name__ == '__main__':
    # Получаем порт из переменной окружения Render
    port = int(os.environ.get('PORT', 10000))
    
    logger.info("🚀 Запуск Folkvang Boss Tracker Server...")
    logger.info(f"📡 WebSocket сервер работает на порту {port}")
    logger.info(f"🔧 Используется worker: gevent")
    
    # Запускаем сервер
    socketio.run(
        app,
        host='0.0.0.0',
        port=port,
        debug=False,
        log_output=True,
        allow_unsafe_werkzeug=True
    )