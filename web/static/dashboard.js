/**
 * Hyperliquid Trading Dashboard - JavaScript
 * Phase 8 - Real-time updates with WebSocket and REST API
 */

// ============================================================================
// Global State
// ============================================================================

const state = {
    ws: null,
    chart: null,
    candleSeries: null,
    equityChart: null,
    equitySeries: null,
    drawdownSeries: null,
    currentSymbol: 'XAU',
    currentTimeframe: '1h',
    currentEquityPeriod: '7d',
    positions: [],
    strategies: [],
    reconnectAttempts: 0,
    maxReconnectAttempts: 10,
    // Voice alerts
    voiceEnabled: false,
    voiceSynth: window.speechSynthesis || null,
    lastAnnouncedTradeId: null
};

// ============================================================================
// Initialization
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
    console.log('Dashboard initializing...');

    // Initialize charts
    initChart();
    initEquityChart();

    // Initialize voice alerts
    initVoiceAlerts();

    // Connect WebSocket
    connectWebSocket();

    // Load initial data
    loadInitialData();

    // Setup event listeners
    setupEventListeners();
});

// ============================================================================
// WebSocket Connection
// ============================================================================

function connectWebSocket() {
    const wsUrl = `ws://${window.location.host}/ws`;
    console.log('Connecting to WebSocket:', wsUrl);

    try {
        state.ws = new WebSocket(wsUrl);

        state.ws.onopen = () => {
            console.log('WebSocket connected');
            state.reconnectAttempts = 0;
        };

        state.ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                handleWebSocketMessage(msg);
            } catch (e) {
                console.error('Failed to parse WS message:', e);
            }
        };

        state.ws.onclose = () => {
            console.log('WebSocket disconnected');
            scheduleReconnect();
        };

        state.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
        };
    } catch (e) {
        console.error('Failed to create WebSocket:', e);
        scheduleReconnect();
    }
}

function scheduleReconnect() {
    if (state.reconnectAttempts < state.maxReconnectAttempts) {
        const delay = Math.min(1000 * Math.pow(2, state.reconnectAttempts), 30000);
        state.reconnectAttempts++;
        console.log(`Reconnecting in ${delay}ms (attempt ${state.reconnectAttempts})`);
        setTimeout(connectWebSocket, delay);
    }
}

function handleWebSocketMessage(msg) {
    switch (msg.type) {
        case 'portfolio_update':
            updatePortfolio(msg.data);
            break;
        case 'price_tick':
            updatePrices(msg.data);
            break;
        case 'strategy_status':
            updateStrategyStatus(msg.data);
            break;
        case 'sentiment_article':
            addSentimentCard(msg.data);
            break;
        case 'risk_alert':
            showRiskAlert(msg.data);
            announceRiskAlert(msg.data);
            break;
        case 'trade_executed':
            // Handle real-time trade notifications
            if (msg.data) {
                announceTradeExecuted(msg.data);
            }
            break;
        case 'ping':
            // Keep-alive, ignore
            break;
        default:
            console.log('Unknown WS message type:', msg.type);
    }
}

// ============================================================================
// REST API Calls
// ============================================================================

async function loadInitialData() {
    // Use allSettled so one failing endpoint doesn't block the others
    const results = await Promise.allSettled([
        fetchAPI('/api/portfolio'),
        fetchAPI('/api/positions'),
        fetchAPI('/api/trades?limit=10'),
        fetchAPI('/api/risk'),
        fetchAPI('/api/strategies'),
        fetchAPI(`/api/sentiment/${state.currentSymbol}`),
        fetchAPI('/api/phase13/status'),
        fetchAPI('/api/funding-rates'),
        fetchAPI('/api/metals-factors')
    ]);

    const [portfolio, positions, trades, risk, strategies, sentiment, phase13, fundingRates, metalsFactors] = results.map(r => r.value);

    // Update UI — skip any that failed
    if (portfolio)  updatePortfolio(portfolio);
    if (positions)  renderPositions(positions);
    if (trades)     renderTrades(trades);
    if (risk)       updateRisk(risk);
    if (strategies) renderStrategies(strategies);
    if (sentiment)  renderSentiment(sentiment);
    if (phase13)    updatePhase13(phase13);
    if (fundingRates) renderFundingRates(fundingRates);
    if (metalsFactors) renderMetalsFactors(metalsFactors);

    // Log any endpoints that failed
    results.forEach((r, i) => {
        if (r.status === 'rejected') {
            const endpoints = ['/api/portfolio','/api/positions','/api/trades','/api/risk','/api/strategies','/api/sentiment','/api/phase13/status','/api/funding-rates','/api/metals-factors'];
            console.error(`Failed to load ${endpoints[i]}:`, r.reason);
        }
    });

    // Load candles for chart
    await loadCandles(state.currentSymbol, state.currentTimeframe);

    console.log('Initial data loaded');

    // Poll Phase 13 status every 10 seconds
    setInterval(async () => {
        try {
            const status = await fetchAPI('/api/phase13/status');
            updatePhase13(status);
        } catch (e) {
            console.error('Failed to update Phase 13:', e);
        }
    }, 10000);

    // Poll funding rates every 60 seconds
    setInterval(async () => {
        try {
            const rates = await fetchAPI('/api/funding-rates');
            renderFundingRates(rates);
        } catch (e) {
            console.error('Failed to update funding rates:', e);
        }
    }, 60000);

    // Poll metals factors every 30 seconds
    setInterval(async () => {
        try {
            const factors = await fetchAPI('/api/metals-factors');
            renderMetalsFactors(factors);
        } catch (e) {
            console.error('Failed to update metals factors:', e);
        }
    }, 30000);
}

async function fetchAPI(endpoint) {
    const response = await fetch(endpoint);
    if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
    }
    return response.json();
}

async function loadCandles(symbol, timeframe) {
    try {
        const candles = await fetchAPI(`/api/candles/${symbol}?timeframe=${timeframe}&limit=200`);

        // Convert to Lightweight Charts format
        const data = candles.map((c, i) => ({
            time: c.time || i,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close
        }));

        state.candleSeries.setData(data);
        state.chart.timeScale().fitContent();
    } catch (e) {
        console.error('Failed to load candles:', e);
    }
}

// ============================================================================
// Chart Initialization
// ============================================================================

function initChart() {
    const container = document.getElementById('chart');
    if (!container) return;

    state.chart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: 400,
        layout: {
            background: { type: 'solid', color: '#141b2d' },
            textColor: '#94a3b8'
        },
        grid: {
            vertLines: { color: 'rgba(255, 255, 255, 0.05)' },
            horzLines: { color: 'rgba(255, 255, 255, 0.05)' }
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal
        },
        timeScale: {
            borderColor: 'rgba(255, 255, 255, 0.1)',
            timeVisible: true
        },
        rightPriceScale: {
            borderColor: 'rgba(255, 255, 255, 0.1)'
        }
    });

    state.candleSeries = state.chart.addCandlestickSeries({
        upColor: '#00d4aa',
        downColor: '#ff4757',
        borderUpColor: '#00d4aa',
        borderDownColor: '#ff4757',
        wickUpColor: '#00d4aa',
        wickDownColor: '#ff4757'
    });

    // Resize handler
    window.addEventListener('resize', () => {
        state.chart.resize(container.clientWidth, 400);
        if (state.equityChart) {
            const eqContainer = document.getElementById('equity-chart');
            if (eqContainer) {
                state.equityChart.resize(eqContainer.clientWidth, 200);
            }
        }
    });
}

// ============================================================================
// Equity Chart Initialization
// ============================================================================

function initEquityChart() {
    const container = document.getElementById('equity-chart');
    if (!container) return;

    state.equityChart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: 200,
        layout: {
            background: { type: 'solid', color: '#0f1520' },
            textColor: '#94a3b8'
        },
        grid: {
            vertLines: { color: 'rgba(255, 255, 255, 0.03)' },
            horzLines: { color: 'rgba(255, 255, 255, 0.03)' }
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal
        },
        timeScale: {
            borderColor: 'rgba(255, 255, 255, 0.1)',
            timeVisible: true
        },
        rightPriceScale: {
            borderColor: 'rgba(255, 255, 255, 0.1)',
            scaleMargins: { top: 0.1, bottom: 0.1 }
        }
    });

    // Equity line (area chart)
    state.equitySeries = state.equityChart.addAreaSeries({
        lineColor: '#00d4aa',
        topColor: 'rgba(0, 212, 170, 0.4)',
        bottomColor: 'rgba(0, 212, 170, 0.0)',
        lineWidth: 2
    });

    // Load initial equity data
    loadEquityData(state.currentEquityPeriod);
}

async function loadEquityData(period) {
    try {
        const data = await fetchAPI(`/api/equity-history?period=${period}`);

        if (data.points && data.points.length > 0) {
            const equityData = data.points.map(p => ({
                time: p.time,
                value: p.equity
            }));

            state.equitySeries.setData(equityData);
            state.equityChart.timeScale().fitContent();

            // Update stats
            document.getElementById('equity-peak').textContent = '$' + data.peak_equity.toLocaleString();
            document.getElementById('equity-current').textContent = '$' + data.current_equity.toLocaleString();
            document.getElementById('equity-maxdd').textContent = '-' + data.max_drawdown.toFixed(1) + '%';

            // Color current equity based on profit/loss
            const currentEl = document.getElementById('equity-current');
            if (data.current_equity >= data.peak_equity * 0.99) {
                currentEl.className = 'value positive';
            } else if (data.current_equity < data.peak_equity * 0.95) {
                currentEl.className = 'value negative';
            } else {
                currentEl.className = 'value';
            }
        }
    } catch (e) {
        console.error('Failed to load equity data:', e);
    }
}

// ============================================================================
// Voice Alerts System
// ============================================================================

function initVoiceAlerts() {
    const toggle = document.getElementById('voice-toggle');
    if (!toggle) return;

    // Load saved preference
    const saved = localStorage.getItem('voiceAlertsEnabled');
    state.voiceEnabled = saved === 'true';
    updateVoiceToggleUI();

    toggle.addEventListener('click', () => {
        state.voiceEnabled = !state.voiceEnabled;
        localStorage.setItem('voiceAlertsEnabled', state.voiceEnabled);
        updateVoiceToggleUI();

        // Announce state change
        if (state.voiceEnabled && state.voiceSynth) {
            speak('Voice alerts enabled');
        }
    });
}

function updateVoiceToggleUI() {
    const toggle = document.getElementById('voice-toggle');
    const label = toggle.querySelector('.voice-label');
    const waves = document.getElementById('voice-waves');

    if (state.voiceEnabled) {
        toggle.classList.add('active');
        label.textContent = 'Voice On';
        if (waves) waves.style.opacity = '1';
    } else {
        toggle.classList.remove('active');
        label.textContent = 'Voice Off';
        if (waves) waves.style.opacity = '0.3';
    }
}

function speak(text) {
    if (!state.voiceEnabled || !state.voiceSynth) return;

    // Cancel any ongoing speech
    state.voiceSynth.cancel();

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.1;  // Slightly faster
    utterance.pitch = 1.0;
    utterance.volume = 0.8;

    state.voiceSynth.speak(utterance);
}

function announceTradeExecuted(trade) {
    if (!state.voiceEnabled) return;

    // Avoid duplicate announcements
    if (trade.id === state.lastAnnouncedTradeId) return;
    state.lastAnnouncedTradeId = trade.id;

    const side = trade.side.toUpperCase();
    const symbol = trade.symbol;
    const size = trade.size;
    const price = trade.price.toFixed(2);

    speak(`${side} ${size} ${symbol} at ${price}`);
}

function announceRiskAlert(alert) {
    if (!state.voiceEnabled) return;

    const severity = alert.severity || 'warning';
    if (severity === 'critical') {
        speak(`Critical alert: ${alert.message}`);
    } else if (severity === 'high') {
        speak(`Risk alert: ${alert.message}`);
    }
}

// ============================================================================
// UI Update Functions
// ============================================================================

function updatePortfolio(data) {
    // Animate portfolio value
    animateValue('portfolio-equity', data.total_equity, '$', 2);
    animateValue('total-equity', data.total_equity, '$', 2);

    // Today's P&L
    const pnl = data.today_pnl || 0;
    const pnlEl = document.getElementById('today-pnl');
    const dailyPnlEl = document.getElementById('daily-pnl');
    const dailyChange = document.getElementById('daily-change');

    const pnlFormatted = formatCurrency(pnl);
    if (pnlEl) pnlEl.textContent = pnlFormatted;
    if (dailyPnlEl) dailyPnlEl.textContent = pnlFormatted;

    if (dailyChange) {
        dailyChange.classList.toggle('positive', pnl >= 0);
        dailyChange.classList.toggle('negative', pnl < 0);
        const arrow = dailyChange.querySelector('.arrow');
        if (arrow) arrow.textContent = pnl >= 0 ? '▲' : '▼';
    }

    // Unrealized P&L
    const unrealized = data.unrealized_pnl || 0;
    const unrealizedEl = document.getElementById('unrealized-pnl');
    if (unrealizedEl) {
        unrealizedEl.textContent = formatCurrency(unrealized);
        unrealizedEl.className = `stat-value ${unrealized >= 0 ? 'positive' : 'negative'}`;
    }

    // Leverage gauge
    updateLeverageGauge(data.leverage || 0);
}

function updateLeverageGauge(leverage) {
    const needle = document.getElementById('leverage-needle');
    const valueEl = document.getElementById('leverage-value');

    if (needle) {
        // Map 0-10x to -60 to 60 degrees
        const rotation = -60 + (leverage / 10) * 120;
        needle.setAttribute('transform', `rotate(${rotation}, 100, 100)`);
    }

    if (valueEl) {
        valueEl.textContent = leverage.toFixed(1) + 'x';
    }
}

function updatePrices(prices) {
    // Update positions table with new prices
    state.positions.forEach(pos => {
        if (prices[pos.symbol]) {
            pos.mark_price = prices[pos.symbol].mid_price;
            pos.pnl = (pos.mark_price - pos.entry_price) * pos.size * (pos.direction === 'long' ? 1 : -1);
        }
    });

    // Re-render positions with updated P&L
    if (Object.keys(prices).length > 0) {
        renderPositions(state.positions);
    }

    // Update chart with latest price for current symbol
    if (prices[state.currentSymbol] && state.candleSeries) {
        const price = prices[state.currentSymbol].mid_price;
        // Update last candle (simplified - in production, merge with timeframe)
        const lastData = state.candleSeries.data();
        if (lastData && lastData.length > 0) {
            const last = lastData[lastData.length - 1];
            state.candleSeries.update({
                time: last.time,
                open: last.open,
                high: Math.max(last.high, price),
                low: Math.min(last.low, price),
                close: price
            });
        }
    }
}

function renderPositions(positions) {
    state.positions = positions;
    const tbody = document.getElementById('positions-body');
    if (!tbody) return;

    tbody.innerHTML = positions.map(pos => {
        // Determine liquidation distance color class
        const liqDist = pos.liq_distance_pct || 0;
        let liqClass = 'liq-safe';      // > 15% = green
        if (liqDist <= 5) {
            liqClass = 'liq-danger';     // <= 5% = red (critical)
        } else if (liqDist <= 10) {
            liqClass = 'liq-warning';    // 5-10% = orange
        } else if (liqDist <= 15) {
            liqClass = 'liq-caution';    // 10-15% = yellow
        }

        const liqPrice = pos.liquidation_price ? `$${pos.liquidation_price.toFixed(2)}` : '-';
        const liqDistDisplay = pos.liq_distance_pct ? `${pos.liq_distance_pct.toFixed(1)}%` : '-';

        return `
            <tr>
                <td>
                    <span class="direction-badge ${pos.direction}">${pos.direction}</span>
                    ${pos.symbol}
                </td>
                <td>${pos.size.toFixed(2)}</td>
                <td>$${pos.entry_price.toFixed(2)}</td>
                <td>$${pos.mark_price.toFixed(2)}</td>
                <td class="${pos.pnl >= 0 ? 'positive' : 'negative'}">
                    ${formatCurrency(pos.pnl)}
                </td>
                <td class="${liqClass}" title="Liq Price: ${liqPrice}">
                    ${liqDistDisplay}
                </td>
            </tr>
        `;
    }).join('');
}

function renderTrades(trades, announceNew = false) {
    const container = document.getElementById('trades-list');
    if (!container) return;

    // Check for new trades to announce
    if (announceNew && trades.length > 0) {
        const newestTrade = trades[0];
        if (newestTrade.id !== state.lastAnnouncedTradeId) {
            announceTradeExecuted(newestTrade);
        }
    }

    container.innerHTML = trades.map(trade => `
        <div class="trade-item">
            <div class="trade-info">
                <span class="trade-symbol">${trade.symbol}</span>
                <span class="trade-time">${formatTime(trade.timestamp)}</span>
            </div>
            <div class="trade-details">
                <span class="trade-side ${trade.side}">${trade.side.toUpperCase()}</span>
                <span>${trade.size} @ $${trade.price.toFixed(2)}</span>
            </div>
        </div>
    `).join('');
}

function updateRisk(data) {
    const equity = 100000; // Default equity for calculations

    document.getElementById('var-95').textContent = formatCurrency(data.var_95 * equity);
    document.getElementById('var-99').textContent = formatCurrency(data.var_99 * equity);
    document.getElementById('cvar-95').textContent = formatCurrency(data.cvar_95 * equity);

    // Update Sharpe gauge
    const sharpeValue = 1.85; // Would come from strategy performance
    document.getElementById('sharpe-value').textContent = sharpeValue.toFixed(2);

    // Update Drawdown gauge
    const drawdown = (data.max_drawdown * 100).toFixed(1);
    document.getElementById('drawdown-value').textContent = `-${drawdown}%`;
}

function renderStrategies(strategies) {
    state.strategies = strategies;
    const container = document.getElementById('strategy-buttons');
    if (!container) return;

    container.innerHTML = strategies.map(strat => `
        <button class="strategy-btn ${strat.status === 'running' ? 'active' : ''}" 
                data-strategy="${strat.name}"
                onclick="toggleStrategy('${strat.name}')">
            <span class="name">${formatStrategyName(strat.name)}</span>
            <span class="status ${strat.status}">${strat.status}</span>
        </button>
    `).join('');
}

function updateStrategyStatus(data) {
    const btn = document.querySelector(`[data-strategy="${data.name}"]`);
    if (btn) {
        btn.classList.toggle('active', data.status === 'running');
        const statusEl = btn.querySelector('.status');
        if (statusEl) {
            statusEl.textContent = data.status;
            statusEl.className = `status ${data.status}`;
        }
    }
}

function renderSentiment(data) {
    const container = document.getElementById('news-scroll');
    if (!container || !data.recent_articles) return;

    container.innerHTML = data.recent_articles.map(article =>
        createSentimentCard(article)
    ).join('');
}

function addSentimentCard(article) {
    const container = document.getElementById('news-scroll');
    if (!container) return;

    const card = document.createElement('div');
    card.innerHTML = createSentimentCard(article);
    container.insertBefore(card.firstChild, container.firstChild);

    // Limit to 10 cards
    while (container.children.length > 10) {
        container.removeChild(container.lastChild);
    }
}

function createSentimentCard(article) {
    const sentiment = getSentimentClass(article.sentiment_score);
    return `
        <div class="news-card">
            <div class="headline">${article.title}</div>
            <div class="news-meta">
                <span class="sentiment-badge ${sentiment}">${sentiment}</span>
                <span class="news-time">${formatTime(article.published_at)}</span>
            </div>
        </div>
    `;
}

function showRiskAlert(data) {
    console.warn('Risk Alert:', data);
    // Could show a toast notification
}

// ============================================================================
// Strategy Control
// ============================================================================

async function toggleStrategy(name) {
    const strat = state.strategies.find(s => s.name === name);
    if (!strat) return;

    const action = strat.status === 'running' ? 'stop' : 'start';
    const btn = document.querySelector(`[data-strategy="${name}"]`);

    try {
        btn.disabled = true;
        const response = await fetch(`/api/strategies/${name}/${action}`, {
            method: 'POST'
        });

        if (response.ok) {
            strat.status = action === 'start' ? 'running' : 'stopped';
            renderStrategies(state.strategies);
        }
    } catch (e) {
        console.error('Failed to toggle strategy:', e);
    } finally {
        btn.disabled = false;
    }
}

// ============================================================================
// Event Listeners
// ============================================================================

function setupEventListeners() {
    // Chart symbol tabs
    document.querySelectorAll('.chart-tabs .tab').forEach(tab => {
        tab.addEventListener('click', async (e) => {
            const symbol = e.target.dataset.symbol;
            if (symbol && symbol !== state.currentSymbol) {
                document.querySelectorAll('.chart-tabs .tab').forEach(t => t.classList.remove('active'));
                e.target.classList.add('active');
                state.currentSymbol = symbol;
                await loadCandles(symbol, state.currentTimeframe);
            }
        });
    });

    // Timeframe buttons
    document.querySelectorAll('.tf-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const tf = e.target.dataset.tf;
            if (tf && tf !== state.currentTimeframe) {
                document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                state.currentTimeframe = tf;
                await loadCandles(state.currentSymbol, tf);
            }
        });
    });

    // Equity period buttons
    document.querySelectorAll('.period-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const period = e.target.dataset.period;
            if (period && period !== state.currentEquityPeriod) {
                document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                state.currentEquityPeriod = period;
                await loadEquityData(period);
            }
        });
    });
}

// ============================================================================
// Utility Functions
// ============================================================================

function formatCurrency(value) {
    const sign = value >= 0 ? '+' : '';
    return sign + '$' + Math.abs(value).toLocaleString('en-US', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
}

function formatTime(timestamp) {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    const now = new Date();
    const diff = (now - date) / 1000 / 60; // minutes

    if (diff < 60) return `${Math.floor(diff)}m ago`;
    if (diff < 1440) return `${Math.floor(diff / 60)}h ago`;
    return date.toLocaleDateString();
}

function formatStrategyName(name) {
    return name.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
}

function getSentimentClass(score) {
    if (score > 0.2) return 'positive';
    if (score < -0.2) return 'negative';
    return 'neutral';
}

function animateValue(elementId, newValue, prefix = '', decimals = 0) {
    const el = document.getElementById(elementId);
    if (!el) return;

    const currentText = el.textContent.replace(/[^0-9.-]/g, '');
    const current = parseFloat(currentText) || 0;
    const target = newValue;
    const duration = 300;
    const start = performance.now();

    function update(now) {
        const elapsed = now - start;
        const progress = Math.min(elapsed / duration, 1);

        // Ease out quad
        const eased = 1 - (1 - progress) * (1 - progress);
        const value = current + (target - current) * eased;

        el.textContent = prefix + value.toLocaleString('en-US', {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals
        });

        // Flash color on change
        if (progress < 1) {
            requestAnimationFrame(update);
        } else {
            el.classList.remove('value-up', 'value-down');
        }
    }

    // Add change indicator
    if (target > current) {
        el.classList.add('value-up');
        el.classList.remove('value-down');
    } else if (target < current) {
        el.classList.add('value-down');
        el.classList.remove('value-up');
    }

    requestAnimationFrame(update);
}

// ============================================================================
// Phase 13 Monitoring
// ============================================================================

function updatePhase13(data) {
    if (!data) return;

    // Update cumulative stats
    const totalTrades = data.cumulative?.total_trades || 0;
    const winRate = data.cumulative?.win_rate || 0;
    const totalPnl = data.cumulative?.total_pnl || 0;

    document.getElementById('p13-total-trades').textContent = totalTrades;
    document.getElementById('p13-win-rate').textContent = winRate.toFixed(1) + '%';

    const pnlEl = document.getElementById('p13-total-pnl');
    pnlEl.textContent = formatCurrency(totalPnl);
    pnlEl.className = `value ${totalPnl >= 0 ? 'positive' : 'negative'}`;

    // Update testnet track
    const testnet = data.testnet_crypto || {};
    const testnetStats = testnet.stats || {};

    const testnetStatus = document.getElementById('testnet-status');
    if (testnetStatus) {
        testnetStatus.textContent = (testnet.status || 'stopped').toUpperCase();
        testnetStatus.className = `status-badge ${testnet.status || 'stopped'}`;
    }

    document.getElementById('testnet-trades').textContent = testnetStats.total_trades || 0;
    document.getElementById('testnet-wr').textContent = (testnetStats.win_rate || 0).toFixed(0) + '%';

    const testnetPnl = document.getElementById('testnet-pnl');
    const testnetPnlVal = testnetStats.total_pnl || 0;
    testnetPnl.textContent = '$' + testnetPnlVal.toFixed(0);
    testnetPnl.className = `value ${testnetPnlVal >= 0 ? 'positive' : 'negative'}`;

    // Update mock track
    const mock = data.mock_metals || {};
    const mockStats = mock.stats || {};

    const mockStatus = document.getElementById('mock-status');
    if (mockStatus) {
        mockStatus.textContent = (mock.status || 'stopped').toUpperCase();
        mockStatus.className = `status-badge ${mock.status || 'stopped'}`;
    }

    document.getElementById('mock-trades').textContent = mockStats.total_trades || 0;
    document.getElementById('mock-wr').textContent = (mockStats.win_rate || 0).toFixed(0) + '%';

    const mockPnl = document.getElementById('mock-pnl');
    const mockPnlVal = mockStats.total_pnl || 0;
    mockPnl.textContent = '$' + mockPnlVal.toFixed(0);
    mockPnl.className = `value ${mockPnlVal >= 0 ? 'positive' : 'negative'}`;

    // Update timestamp
    const timestamp = data.timestamp ? new Date(data.timestamp) : new Date();
    document.getElementById('p13-last-update').textContent = 'Last update: ' + timestamp.toLocaleTimeString();
}

// ============================================================================
// Funding Rates Widget
// ============================================================================

function renderFundingRates(data) {
    const container = document.getElementById('funding-rates-list');
    if (!container || !data || !data.rates) return;

    if (data.rates.length === 0) {
        container.innerHTML = '<div class="loading-placeholder">No funding data available</div>';
        return;
    }

    container.innerHTML = data.rates.map(rate => {
        const rateClass = rate.rate_8h >= 0 ? 'pay' : 'receive';
        const rateSign = rate.rate_8h >= 0 ? '+' : '';
        const annualSign = rate.rate_annual_pct >= 0 ? '+' : '';

        return `
            <div class="funding-rate-item">
                <span class="symbol">${rate.symbol}</span>
                <span class="rate ${rateClass}">${rateSign}${rate.rate_8h_pct.toFixed(4)}%</span>
                <span class="annual">${annualSign}${rate.rate_annual_pct.toFixed(1)}%/yr</span>
            </div>
        `;
    }).join('');
}

// ============================================================================
// Metals Ratios Widget
// ============================================================================

function renderMetalsFactors(data) {
    if (!data) return;

    // Update Gold/Silver Ratio
    const ratioEl = document.getElementById('gold-silver-ratio');
    if (ratioEl && data.gold_silver_ratio !== null) {
        ratioEl.textContent = data.gold_silver_ratio.toFixed(2);
    }

    // Update Z-Score with color coding
    const zscoreEl = document.getElementById('gold-silver-zscore');
    if (zscoreEl && data.gold_silver_zscore !== null) {
        const zscore = data.gold_silver_zscore;
        zscoreEl.textContent = (zscore >= 0 ? '+' : '') + zscore.toFixed(2);

        // Color code based on significance
        zscoreEl.classList.remove('positive', 'negative', 'neutral');
        if (zscore > 1.5) {
            zscoreEl.classList.add('positive');  // Silver undervalued
        } else if (zscore < -1.5) {
            zscoreEl.classList.add('negative');  // Gold undervalued
        } else {
            zscoreEl.classList.add('neutral');
        }
    }

    // Update Signal Badge
    const signalEl = document.getElementById('metals-signal');
    if (signalEl && data.signal) {
        const signalLabels = {
            'neutral': 'Neutral',
            'silver_undervalued': 'Silver Undervalued',
            'gold_undervalued': 'Gold Undervalued'
        };
        signalEl.textContent = signalLabels[data.signal] || data.signal;
        signalEl.className = 'signal-badge ' + data.signal;
    }

    // Update the visual bar (50% = neutral, >50% = gold heavy, <50% = silver heavy)
    const barFill = document.getElementById('metals-bar-fill');
    if (barFill && data.gold_silver_zscore !== null) {
        // Convert z-score to percentage (z-score of 0 = 50%, +3 = 75%, -3 = 25%)
        const zscore = data.gold_silver_zscore;
        const percentage = Math.max(10, Math.min(90, 50 + (zscore * 10)));
        barFill.style.width = percentage + '%';
    }
}
