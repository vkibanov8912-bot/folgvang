#!/usr/bin/env python3
"""
Folkvang Boss Tracker Server for Render.com
"""

from flask import render_template_string
from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit
from datetime import datetime, timedelta
import json
import logging
from threading import Lock
import os

# НЕ используем eventlet для асинхронности
# УБЕРИТЕ эти строки:
# import eventlet
# eventlet.monkey_patch()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'folkvang-secret-key-2024')

# Настройка CORS для Render - используем threading вместо eventlet
socketio = SocketIO(
    app, 
    cors_allowed_origins="*",
    async_mode='threading',  # МЕНЯЕМ НА threading
    logger=True,
    engineio_logger=True
)

# ... остальной код остается БЕЗ ИЗМЕНЕНИЙ ...

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
                self.bosses[floor_key][bossType] = {
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
    # Простая проверка токена
    auth_token = request.headers.get('X-Auth-Token')
    expected_token = os.environ.get('ADMIN_TOKEN', 'admin123')
    
    if auth_token != expected_token:
        return jsonify({'error': 'Unauthorized'}), 401
    
    if storage.reset_all():
        socketio.emit('reset_all', {}, broadcast=True)
        return jsonify({'success': True})
    
    return jsonify({'error': 'Reset failed'}), 500
    @app.route('/app')
def web_app():
    """Отдает HTML интерфейс"""
    html_content = """
    <!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Фолькванг - Синхронизированный трекер</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
    <style>
        /* Все стили из вашего index.html */
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: linear-gradient(135deg, #0f172a, #1e293b); color: white; font-family: sans-serif; }
        .container { max-width: 500px; margin: 0 auto; padding: 20px; }
        /* ... добавьте ВСЕ стили из вашего index.html ... */
    </style>
</head>
<body>
    <div class="container">
        <!-- Весь HTML из вашего index.html -->
        <div id="serverStatus" class="server-status disconnected">
            <div class="status-dot"></div>
            <span id="serverStatusText">Сервер: отключен</span>
        </div>
        
        <div class="header">
            <h1>🏰 Фолькванг</h1>
            <p>Синхронизированный трекер боссов</p>
        </div>
        
        <!-- Вкладки этажей -->
        <div class="tabs" id="floorTabs">
            <div class="tab active" data-floor="1">1 этаж</div>
            <div class="tab" data-floor="2">2 этаж</div>
            <div class="tab" data-floor="3">3 этаж</div>
            <div class="tab" data-floor="4">4 этаж</div>
        </div>
        
        <!-- Этаж 1 -->
        <div class="floor-section active" id="floor1">
            <div class="boss-grid">
                <div class="boss-card" data-floor="1" data-boss="mage">
                    <div class="boss-name">Вёльва</div>
                    <div class="boss-timer" id="floor1-mage">--:--</div>
                    <div class="timer-text">До возрождения</div>
                </div>
                <div class="boss-card" data-floor="1" data-boss="healer">
                    <div class="boss-name">Скальд</div>
                    <div class="boss-timer" id="floor1-healer">--:--</div>
                    <div class="timer-text">До возрождения</div>
                </div>
                <div class="boss-card" data-floor="1" data-boss="spearman">
                    <div class="boss-name">Копейщик</div>
                    <div class="boss-timer" id="floor1-spearman">--:--</div>
                    <div class="timer-text">До возрождения</div>
                </div>
                <div class="boss-card" data-floor="1" data-boss="berserk">
                    <div class="boss-name">Берсерк</div>
                    <div class="boss-timer" id="floor1-berserk">--:--</div>
                    <div class="timer-text">До возрождения</div>
                </div>
            </div>
        </div>
        
        <!-- Этаж 2 -->
        <div class="floor-section" id="floor2">
            <div class="boss-grid">
                <div class="boss-card" data-floor="2" data-boss="mage">
                    <div class="boss-name">Вёльва</div>
                    <div class="boss-timer" id="floor2-mage">--:--</div>
                    <div class="timer-text">До возрождения</div>
                </div>
                <div class="boss-card" data-floor="2" data-boss="healer">
                    <div class="boss-name">Скальд</div>
                    <div class="boss-timer" id="floor2-healer">--:--</div>
                    <div class="timer-text">До возрождения</div>
                </div>
                <div class="boss-card" data-floor="2" data-boss="spearman">
                    <div class="boss-name">Копейщик</div>
                    <div class="boss-timer" id="floor2-spearman">--:--</div>
                    <div class="timer-text">До возрождения</div>
                </div>
                <div class="boss-card" data-floor="2" data-boss="berserk">
                    <div class="boss-name">Берсерк</div>
                    <div class="boss-timer" id="floor2-berserk">--:--</div>
                    <div class="timer-text">До возрождения</div>
                </div>
            </div>
        </div>
        
        <!-- Этаж 3 -->
        <div class="floor-section" id="floor3">
            <div class="boss-grid">
                <div class="boss-card" data-floor="3" data-boss="mage">
                    <div class="boss-name">Вёльва</div>
                    <div class="boss-timer" id="floor3-mage">--:--</div>
                    <div class="timer-text">До возрождения</div>
                </div>
                <div class="boss-card" data-floor="3" data-boss="healer">
                    <div class="boss-name">Скальд</div>
                    <div class="boss-timer" id="floor3-healer">--:--</div>
                    <div class="timer-text">До возрождения</div>
                </div>
                <div class="boss-card" data-floor="3" data-boss="spearman">
                    <div class="boss-name">Копейщик</div>
                    <div class="boss-timer" id="floor3-spearman">--:--</div>
                    <div class="timer-text">До возрождения</div>
                </div>
                <div class="boss-card" data-floor="3" data-boss="berserk">
                    <div class="boss-name">Берсерк</div>
                    <div class="boss-timer" id="floor3-berserk">--:--</div>
                    <div class="timer-text">До возрождения</div>
                </div>
            </div>
        </div>
        
        <!-- Этаж 4 -->
        <div class="floor-section" id="floor4">
            <div class="boss-grid">
                <div class="boss-card" data-floor="4" data-boss="mage">
                    <div class="boss-name">Вёльва</div>
                    <div class="boss-timer" id="floor4-mage">--:--</div>
                    <div class="timer-text">До возрождения</div>
                </div>
                <div class="boss-card" data-floor="4" data-boss="healer">
                    <div class="boss-name">Скальд</div>
                    <div class="boss-timer" id="floor4-healer">--:--</div>
                    <div class="timer-text">До возрождения</div>
                </div>
                <div class="boss-card" data-floor="4" data-boss="spearman">
                    <div class="boss-name">Копейщик</div>
                    <div class="boss-timer" id="floor4-spearman">--:--</div>
                    <div class="timer-text">До возрождения</div>
                </div>
                <div class="boss-card" data-floor="4" data-boss="berserk">
                    <div class="boss-name">Берсерк</div>
                    <div class="boss-timer" id="floor4-berserk">--:--</div>
                    <div class="timer-text">До возрождения</div>
                </div>
            </div>
        </div>
        
        <!-- Подвал -->
        <div class="footer">
            <p>Последнее обновление: <span id="lastUpdateTime">--:--:--</span></p>
            <p>Сервер: folgvang-2.onrender.com</p>
        </div>
        
        <!-- Модальное окно -->
        <div class="modal" id="bossModal">
            <div class="modal-content">
                <h2 id="modalBossName">Вёльва</h2>
                <p style="text-align: center; margin-bottom: 20px;" id="modalBossFloor">Этаж 1</p>
                <p style="text-align: center; margin-bottom: 20px; color: #94a3b8;">
                    Подтвердите убийство босса. Это обновит таймер у всех игроков.
                </p>
                <div class="modal-buttons">
                    <button class="btn btn-secondary" onclick="closeModal()">Отмена</button>
                    <button class="btn btn-primary" onclick="markBossKilled()">Подтвердить убийство</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        // ВСЕГДА используйте этот URL для сервера
        const SERVER_URL = window.location.origin;
        
        // ===== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =====
        const tg = window.Telegram.WebApp || {};
        let currentBoss = null;
        
        // Данные боссов (локальное хранилище)
        const bossesData = {
            floor1: {
                mage: { name: 'Вёльва', lastKill: null },
                healer: { name: 'Скальд', lastKill: null },
                spearman: { name: 'Копейщик', lastKill: null },
                berserk: { name: 'Берсерк', lastKill: null }
            },
            floor2: {
                mage: { name: 'Вёльва', lastKill: null },
                healer: { name: 'Скальд', lastKill: null },
                spearman: { name: 'Копейщик', lastKill: null },
                berserk: { name: 'Берсерк', lastKill: null }
            },
            floor3: {
                mage: { name: 'Вёльва', lastKill: null },
                healer: { name: 'Скальд', lastKill: null },
                spearman: { name: 'Копейщик', lastKill: null },
                berserk: { name: 'Берсерк', lastKill: null }
            },
            floor4: {
                mage: { name: 'Вёльва', lastKill: null },
                healer: { name: 'Скальд', lastKill: null },
                spearman: { name: 'Копейщик', lastKill: null },
                berserk: { name: 'Берсерк', lastKill: null }
            }
        };
        
        // ===== WebSocket КЛИЕНТ =====
        class RenderServer {
            constructor() {
                this.socket = null;
                this.connected = false;
                this.serverUrl = SERVER_URL;
            }
            
            connect() {
                try {
                    console.log(`🔄 Подключение к ${this.serverUrl}`);
                    this.socket = io(this.serverUrl, {
                        transports: ['websocket', 'polling'],
                        reconnection: true,
                        reconnectionAttempts: 5,
                        reconnectionDelay: 1000
                    });
                    
                    this.socket.on('connect', () => {
                        console.log('✅ Успешное подключение к серверу');
                        this.connected = true;
                        this.updateStatus('connected', 'Синхронизировано');
                    });
                    
                    this.socket.on('connect_error', (error) => {
                        console.error('Ошибка подключения:', error);
                        this.connected = false;
                        this.updateStatus('disconnected', 'Ошибка подключения');
                    });
                    
                    this.socket.on('disconnect', (reason) => {
                        console.log(`❌ Соединение закрыто: ${reason}`);
                        this.connected = false;
                        this.updateStatus('disconnected', 'Потеряно соединение');
                    });
                    
                    this.socket.on('boss_update', (data) => {
                        this.processKill(data);
                    });
                    
                    this.socket.on('initial_state', (data) => {
                        this.syncWithServer(data);
                    });
                    
                    this.socket.on('state_update', (data) => {
                        this.syncWithServer(data);
                    });
                    
                    this.socket.on('kill_confirmed', (data) => {
                        if (data.success) {
                            alert('✅ Убийство синхронизировано с сервером');
                        }
                    });
                    
                } catch (error) {
                    console.error('Ошибка подключения:', error);
                    this.updateStatus('disconnected', 'Ошибка подключения');
                }
            }
            
            processKill(killData) {
                const bossNames = {
                    'mage': 'Вёльва',
                    'healer': 'Скальд',
                    'spearman': 'Копейщик',
                    'berserk': 'Берсерк'
                };
                
                const floorId = `floor${killData.floor}`;
                const bossType = killData.boss;
                const bossName = bossNames[bossType] || bossType;
                
                if (bossesData[floorId] && bossesData[floorId][bossType]) {
                    const boss = bossesData[floorId][bossType];
                    const killTime = new Date(killData.kill_time).getTime();
                    
                    if (!boss.lastKill || killTime > boss.lastKill) {
                        boss.lastKill = killTime;
                        saveToLocalStorage();
                        updateBossTimer(floorId, bossType, false);
                        
                        // Показываем уведомление
                        const currentPlayer = tg.initDataUnsafe?.user?.first_name || 'Игрок';
                        if (killData.player !== currentPlayer) {
                            this.showKillNotification(bossName, killData.floor, killData.player);
                        }
                    }
                }
            }
            
            syncWithServer(serverState) {
                console.log('🔄 Синхронизация с сервером');
                let updated = false;
                
                Object.keys(serverState).forEach(floorId => {
                    Object.keys(serverState[floorId]).forEach(bossType => {
                        const serverBoss = serverState[floorId][bossType];
                        const localBoss = bossesData[floorId]?.[bossType];
                        
                        if (serverBoss && localBoss && serverBoss.kill_time) {
                            const killTime = new Date(serverBoss.kill_time).getTime();
                            
                            if (!localBoss.lastKill || killTime > localBoss.lastKill) {
                                localBoss.lastKill = killTime;
                                updated = true;
                            }
                        }
                    });
                });
                
                if (updated) {
                    saveToLocalStorage();
                    updateAllTimers();
                    console.log('✅ Данные синхронизированы');
                }
            }
            
            sendKill(floorId, bossType, playerName) {
                if (!this.connected || !this.socket) {
                    return { success: false, message: 'Нет соединения с сервером' };
                }
                
                const floorNum = floorId.replace('floor', '');
                
                this.socket.emit('boss_kill', {
                    floor: parseInt(floorNum),
                    boss: bossType,
                    player: playerName
                });
                
                return { success: true, message: 'Отправлено на сервер' };
            }
            
            showKillNotification(bossName, floor, player) {
                alert(`🎯 ${bossName} убит на этаже ${floor} игроком ${player}`);
            }
            
            updateStatus(status, text) {
                const element = document.getElementById('serverStatus');
                const textElement = document.getElementById('serverStatusText');
                
                if (element && textElement) {
                    element.className = `server-status ${status}`;
                    textElement.textContent = text;
                }
            }
        }
        
        const renderServer = new RenderServer();
        
        // ===== ЛОКАЛЬНОЕ ХРАНИЛИЩЕ =====
        function saveToLocalStorage() {
            localStorage.setItem('folkvang_boss_data', JSON.stringify(bossesData));
        }
        
        function loadFromLocalStorage() {
            const saved = localStorage.getItem('folkvang_boss_data');
            if (saved) {
                try {
                    const parsed = JSON.parse(saved);
                    Object.assign(bossesData, parsed);
                } catch (e) {
                    console.log('Ошибка загрузки данных:', e);
                }
            }
        }
        
        // ===== ТАЙМЕРЫ =====
        function updateAllTimers() {
            for (let floor = 1; floor <= 4; floor++) {
                const floorId = `floor${floor}`;
                ['mage', 'healer', 'spearman', 'berserk'].forEach(bossType => {
                    updateBossTimer(floorId, bossType, false);
                });
            }
        }
        
        function updateBossTimer(floorId, bossType, isJustKilled) {
            const boss = bossesData[floorId][bossType];
            const timerElement = document.getElementById(`${floorId}-${bossType}`);
            if (!timerElement) return;
            
            if (!boss.lastKill) {
                timerElement.textContent = '🎉 Жив';
                timerElement.style.color = '#10b981';
                return;
            }
            
            const killTime = new Date(boss.lastKill);
            const respawnTime = new Date(killTime.getTime() + 120 * 60 * 1000);
            const now = new Date();
            
            if (now >= respawnTime) {
                timerElement.textContent = '🎉 Жив';
                timerElement.style.color = '#10b981';
            } else {
                const diff = respawnTime - now;
                const minutes = Math.floor(diff / 60000);
                const seconds = Math.floor((diff % 60000) / 1000);
                
                timerElement.textContent = `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
                timerElement.style.color = minutes < 5 ? '#ef4444' : '#f59e0b';
            }
        }
        
        function updateLastUpdateTime() {
            const now = new Date();
            const timeString = now.toLocaleTimeString('ru-RU', {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            });
            document.getElementById('lastUpdateTime').textContent = timeString;
        }
        
        // ===== ИНТЕРФЕЙС =====
        function showModal(floorId, bossType) {
            const boss = bossesData[floorId][bossType];
            const floorNum = floorId.replace('floor', '');
            
            document.getElementById('modalBossName').textContent = boss.name;
            document.getElementById('modalBossFloor').textContent = `Этаж ${floorNum}`;
            currentBoss = { floorId, bossType };
            
            document.getElementById('bossModal').classList.add('active');
        }
        
        function closeModal() {
            document.getElementById('bossModal').classList.remove('active');
            currentBoss = null;
        }
        
        async function markBossKilled() {
            if (!currentBoss) return;
            
            const { floorId, bossType } = currentBoss;
            const boss = bossesData[floorId][bossType];
            const playerName = tg.initDataUnsafe?.user?.first_name || 'Игрок';
            
            // 1. Обновляем локально
            boss.lastKill = Date.now();
            saveToLocalStorage();
            
            // 2. Отправляем на сервер
            const result = renderServer.sendKill(floorId, bossType, playerName);
            
            // 3. Показываем уведомление
            if (result.success) {
                alert(`✅ ${boss.name} убит! Все игроки получат обновление.`);
            } else {
                alert(`✅ ${boss.name} отмечен локально. ${result.message}`);
            }
            
            // 4. Обновляем интерфейс
            updateBossTimer(floorId, bossType, true);
            
            // 5. Закрываем окно
            closeModal();
        }
        
        // ===== ИНИЦИАЛИЗАЦИЯ =====
        function initApp() {
            // Загружаем данные
            loadFromLocalStorage();
            
            // Настройка вкладок
            document.querySelectorAll('.tab').forEach(tab => {
                tab.addEventListener('click', () => {
                    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                    tab.classList.add('active');
                    
                    const floor = tab.dataset.floor;
                    document.querySelectorAll('.floor-section').forEach(section => {
                        section.classList.remove('active');
                    });
                    document.getElementById(`floor${floor}`).classList.add('active');
                });
            });
            
            // Настройка карточек боссов
            document.querySelectorAll('.boss-card').forEach(card => {
                card.addEventListener('click', () => {
                    const floorId = `floor${card.dataset.floor}`;
                    const bossType = card.dataset.boss;
                    showModal(floorId, bossType);
                });
            });
            
            // Инициализируем таймеры
            updateAllTimers();
            updateLastUpdateTime();
            
            // Запускаем сервер
            renderServer.connect();
            
            // Автообновление
            setInterval(updateAllTimers, 1000);
            setInterval(updateLastUpdateTime, 30000);
            
            // Настройка Telegram
            if (tg.expand) {
                tg.expand();
                tg.setHeaderColor('#0f172a');
                tg.setBackgroundColor('#0f172a');
            }
            
            if (tg.BackButton) {
                tg.BackButton.show();
                tg.BackButton.onClick(() => {
                    if (document.getElementById('bossModal').classList.contains('active')) {
                        closeModal();
                    } else if (tg.close) {
                        tg.close();
                    }
                });
            }
            
            // Показываем информацию о пользователе
            if (tg.initDataUnsafe?.user) {
                const user = tg.initDataUnsafe.user;
                console.log('Telegram User:', user);
            }
        }
        
        // Запуск при загрузке
        document.addEventListener('DOMContentLoaded', initApp);
    </script>
</body>
</html>
    """
    return render_template_string(html_content)

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
    logger.info(f"🔧 Используется async_mode: threading")
    
    # Запускаем сервер
    socketio.run(
        app,
        host='0.0.0.0',
        port=port,
        debug=False,
        log_output=True,
        allow_unsafe_werkzeug=True
    )
