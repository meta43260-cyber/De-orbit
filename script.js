// ===== Main Application Logic =====

let currentSatellite = '25544'; // ISS default
let map = null;
let decayChart = null;
let probabilityChart = null;
let currentPrediction = null;
let orbitPath = null;
let satelliteMarker = null;

// Initialize
document.addEventListener('DOMContentLoaded', async () => {
    initMap();
    await refreshData();
    
    // Auto-refresh ทุก 15 นาที
    setInterval(refreshData, 15 * 60 * 1000);
    
    // Register Service Worker (PWA)
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('sw.js').catch(console.error);
    }
});

async function refreshData() {
    const statusEl = document.getElementById('status');
    statusEl.textContent = 'กำลังอัปเดต...';
    statusEl.className = 'text-yellow-400';
    
    try {
        // 1. ดึง TLE history
        const tleHistory = await API.fetchTLEHistory(currentSatellite, 30);
        
        if (tleHistory.length < 5) {
            throw new Error('ข้อมูล TLE ไม่เพียงพอ');
        }
        
        // 2. ดึง Space Weather
        const [spaceWeather, solarFlux] = await Promise.all([
            API.fetchSpaceWeather(),
            API.fetchSolarFlux()
        ]);
        
        // 3. ดึง Learning Data
        const learningData = window.LearningSystem.getCorrectionData();
        
        // 4. ทำนาย
        const prediction = await Algorithms.predictReentry(
            tleHistory,
            spaceWeather,
            learningData
        );
        
        if (prediction.error) {
            throw new Error(prediction.error);
        }
        
        currentPrediction = prediction;
        
        // 5. บันทึก prediction (สำหรับเรียนรู้)
        window.LearningSystem.recordPrediction(
            currentSatellite,
            prediction,
            tleHistory[tleHistory.length-1].epoch
        );
        
        // 6. แสดงผล
        updateUI(prediction, tleHistory, spaceWeather);
        
        statusEl.textContent = 'อัปเดตสำเร็จ';
        statusEl.className = 'text-green-400';
        
    } catch (error) {
        console.error('Refresh error:', error);
        statusEl.textContent = 'เกิดข้อผิดพลาด';
        statusEl.className = 'text-red-400';
        showError(error.message);
    }
}

function updateUI(prediction, tleHistory, spaceWeather) {
    // Prediction Card
    const reentryDate = new Date(prediction.time);
    const ictDate = new Date(prediction.time + 7 * 60 * 60 * 1000);
    const uncertaintyHours = (prediction.uncertainty / (60 * 60 * 1000)).toFixed(1);
    
    document.getElementById('utcTime').textContent = reentryDate.toLocaleDateString('th-TH', { 
        day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' 
    });
    document.getElementById('utcUncertainty').textContent = `± ${uncertaintyHours} ชม.`;
    
    document.getElementById('ictTime').textContent = ictDate.toLocaleDateString('th-TH', { 
        day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Bangkok' 
    });
    document.getElementById('ictUncertainty').textContent = `± ${uncertaintyHours} ชม.`;
    
    document.getElementById('confidenceBadge').textContent = `${Math.round(prediction.confidence)}%`;
    document.getElementById('confidenceBadge').className = 
        `text-xs px-2 py-1 rounded-full ${getConfidenceColor(prediction.confidence)}`;
    
    // Impact Zone (ประมาณจาก inclination)
    const inclination = prediction.inclination || 51.6;
    document.getElementById('impactZone').textContent = getImpactZone(inclination);
    
    // Details Tab
    document.getElementById('noradId').textContent = currentSatellite;
    document.getElementById('inclination').textContent = `${inclination.toFixed(2)}°`;
    document.getElementById('currentAlt').textContent = `${prediction.currentAltitude?.toFixed(1) || '-'} กม.`;
    document.getElementById('bstar').textContent = `${prediction.bstar?.toExponential(2) || '-'}`;
    document.getElementById('kpIndex').textContent = spaceWeather.kp.toFixed(1);
    document.getElementById('tleAge').textContent = `${prediction.tleAge?.toFixed(1) || '-'} ชม.`;
    document.getElementById('kalmanConf').textContent = `${Math.round(prediction.confidence)}%`;
    document.getElementById('ensembleModels').textContent = prediction.models ? 
        prediction.models.map(m => m.name).join(', ') : (prediction.method || '-');
    
    // Learning Status
    const learningData = window.LearningSystem.getCorrectionData();
    document.getElementById('learningProgress').style.width = `${learningData.learningProgress}%`;
    document.getElementById('learningPercent').textContent = `${Math.round(learningData.learningProgress)}%`;
    document.getElementById('learningInfo').textContent = 
        `เก็บข้อมูลแล้ว ${learningData.matchedCount} ครั้ง / เป้าหมาย 30 ครั้ง | ความแม่นยำ: ${learningData.accuracy.toFixed(0)}%`;
    document.getElementById('learningStatus').textContent = 
        learningData.correctionBias !== 0 ? '✅ ปรับแก้แล้ว' : '⏳ รวบรวมข้อมูล';
    
    // Update Charts & Map
    updateDecayChart(tleHistory);
    updateProbabilityChart(prediction);
    updateMap(tleHistory, prediction);
    
    // Learning History
    updateLearningHistory();
}

function initMap() {
    map = L.map('map').setView([0, 100], 2);
    
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '© OpenStreetMap © CARTO',
        maxZoom: 19
    }).addTo(map);
    
    setTimeout(() => map.invalidateSize(), 100);
}

function updateMap(tleHistory, prediction) {
    if (!map) return;
    
    // Clear existing
    if (orbitPath) map.removeLayer(orbitPath);
    if (satelliteMarker) map.removeLayer(satelliteMarker);
    
    // Calculate orbit using satellite.js
    const currentTLE = tleHistory[tleHistory.length - 1];
    const satrec = satellite.twoline2satrec(currentTLE.line1, currentTLE.line2);
    
    const now = new Date();
    const orbitPoints = [];
    let reentryPoint = null;
    let currentPosition = null;
    
    // Propagate orbit for next 24 hours (or until reentry)
    for (let i = 0; i < 24 * 60; i += 5) { // every 5 minutes
        const time = new Date(now.getTime() + i * 60 * 1000);
        const pv = satellite.propagate(satrec, time);
        
        if (pv.position) {
            const gmst = satellite.gstime(time);
            const gd = satellite.eciToGeodetic(pv.position, gmst);
            
            const lat = satellite.degreesLat(gd.latitude);
            const lon = satellite.degreesLong(gd.longitude);
            const alt = gd.height;
            
            if (alt < 80) {
                // Re-entry!
                reentryPoint = { lat, lon, alt };
                break;
            }
            
            orbitPoints.push([lat, lon]);
            
            if (i === 0) {
                currentPosition = { lat, lon, alt };
            }
        }
    }
    
    // Draw orbit path
    if (orbitPoints.length > 1) {
        orbitPath = L.polyline(orbitPoints, {
            color: '#fbbf24',
            weight: 2,
            opacity: 0.7
        }).addTo(map);
    }
    
    // Draw uncertainty zone
    if (reentryPoint || currentPosition) {
        const center = reentryPoint || currentPosition;
        const uncertaintyKm = prediction.uncertainty / (60 * 60 * 1000) * 7.5; // rough km/hour
        
        L.circle([center.lat, center.lon], {
            radius: uncertaintyKm * 1000,
            color: '#ef4444',
            fillColor: '#ef4444',
            fillOpacity: 0.2,
            weight: 2
        }).addTo(map);
        
        // Impact marker
        if (reentryPoint) {
            L.marker([reentryPoint.lat, reentryPoint.lon], {
                icon: L.divIcon({
                    html: '💥',
                    className: 'text-2xl',
                    iconSize: [30, 30]
                })
            }).addTo(map).bindPopup('จุด Re-entry ที่คาดการณ์');
        }
    }
    
    // Current position marker
    if (currentPosition) {
        satelliteMarker = L.circleMarker([currentPosition.lat, currentPosition.lon], {
            radius: 8,
            fillColor: '#3b82f6',
            color: '#fff',
            weight: 2,
            fillOpacity: 0.9
        }).addTo(map);
        
        map.setView([currentPosition.lat, currentPosition.lon], 3);
    }
}

function updateDecayChart(tleHistory) {
    const ctx = document.getElementById('decayChart').getContext('2d');
    
    if (decayChart) decayChart.destroy();
    
    const labels = tleHistory.map(tle => {
        const date = new Date(tle.epoch);
        return date.toLocaleDateString('th-TH', { day: 'numeric', month: 'short' });
    });
    
    const altitudes = tleHistory.map(tle => 
        Algorithms.extractMeanMotion(tle.line2) ? 
            estimateAltitudeFromMM(Algorithms.extractMeanMotion(tle.line2)) : null
    );
    
    decayChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'ความสูง (กม.)',
                data: altitudes,
                borderColor: '#3b82f6',
                backgroundColor: 'rgba(59, 130, 246, 0.1)',
                tension: 0.4,
                fill: true
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { labels: { color: '#fff' } }
            },
            scales: {
                y: {
                    beginAtZero: false,
                    ticks: { color: '#9ca3af' },
                    grid: { color: 'rgba(255,255,255,0.1)' },
                    title: { display: true, text: 'ความสูง (กม.)', color: '#9ca3af' }
                },
                x: {
                    ticks: { color: '#9ca3af', maxRotation: 45 },
                    grid: { color: 'rgba(255,255,255,0.1)' }
                }
            }
        }
    });
}

function estimateAltitudeFromMM(mm) {
    return Math.max(0, Math.pow(86666 / mm, 2/3) - 6378);
}

function updateProbabilityChart(prediction) {
    const ctx = document.getElementById('probabilityChart').getContext('2d');
    
    if (probabilityChart) probabilityChart.destroy();
    
    const center = new Date(prediction.time);
    const uncertainty = prediction.uncertainty;
    
    const labels = [];
    const data = [];
    
    for (let i = -12; i <= 12; i++) {
        const time = new Date(center.getTime() + i * 60 * 60 * 1000);
        labels.push(time.getUTCHours() + ':00');
        
        // Gaussian distribution
        const x = (i * 60 * 60 * 1000) / uncertainty;
        const prob = Math.exp(-0.5 * x * x);
        data.push(prob * 100);
    }
    
    const gradient = ctx.createLinearGradient(0, 0, 0, 200);
    gradient.addColorStop(0, 'rgba(59, 130, 246, 0.8)');
    gradient.addColorStop(1, 'rgba(59, 130, 246, 0.1)');
    
    probabilityChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'ความน่าจะเป็น',
                data,
                borderColor: '#3b82f6',
                backgroundColor: gradient,
                tension: 0.4,
                fill: true,
                pointRadius: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { labels: { color: '#fff' } }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: { 
                        color: '#9ca3af',
                        callback: v => v.toFixed(0) + '%'
                    },
                    grid: { color: 'rgba(255,255,255,0.1)' }
                },
                x: {
                    ticks: { color: '#9ca3af', maxRotation: 45 },
                    grid: { color: 'rgba(255,255,255,0.1)' }
                }
            }
        }
    });
}

function updateLearningHistory() {
    const history = window.LearningSystem.getLearningHistory();
    const container = document.getElementById('learningHistory');
    
    if (history.length === 0) {
        container.innerHTML = '<p class="text-gray-400 text-center py-8">ยังไม่มีข้อมูลการเรียนรู้</p>';
        return;
    }
    
    container.innerHTML = history.map(h => `
        <div class="glass rounded-lg p-3">
            <div class="flex items-center justify-between mb-1">
                <span class="font-semibold">NORAD ${h.noradId}</span>
                <span class="text-xs ${h.matched ? 'text-green-400' : 'text-yellow-400'}">
                    ${h.matched ? '✅ บันทึกแล้ว' : '⏳ รอผล'}
                </span>
            </div>
            <div class="text-xs text-gray-400">
                <div>ทำนาย: ${h.predictedDate}</div>
                ${h.matched ? `<div class="text-green-400">ความคลาดเคลื่อน: ${h.errorHours} ชม.</div>` : ''}
                <div>ความมั่นใจ: ${h.confidence.toFixed(0)}%</div>
            </div>
        </div>
    `).join('');
}

// ===== UI Helpers =====

function showTab(tabName) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('tab-active'));
    
    document.getElementById(`tab-${tabName}`).classList.remove('hidden');
    event.target.classList.add('tab-active');
    
    if (tabName === 'map' && map) {
        setTimeout(() => map.invalidateSize(), 100);
    }
}

function changeSatellite(value) {
    const customInput = document.getElementById('customInput');
    if (value === '12345') {
        customInput.classList.remove('hidden');
    } else {
        customInput.classList.add('hidden');
        currentSatellite = value;
        refreshData();
    }
}

document.getElementById('customNoradId').addEventListener('blur', (e) => {
    if (e.target.value) {
        currentSatellite = e.target.value;
        refreshData();
    }
});

function getConfidenceColor(confidence) {
    if (confidence >= 70) return 'bg-green-500';
    if (confidence >= 50) return 'bg-yellow-500';
    return 'bg-red-500';
}

function getImpactZone(inclination) {
    if (inclination < 20) return 'เขตร้อน (ไทย/อินโดนีเซีย)';
    if (inclination < 40) return 'กึ่งร้อน (เอเชียใต้)';
    if (inclination < 60) return 'กลาง (ยุโรป/จีน/ญี่ปุ่น)';
    return 'ละติจูดสูง (รัสเซีย/แคนาดา)';
}

function showError(message) {
    // Simple error notification
    const notif = document.createElement('div');
    notif.className = 'fixed top-20 left-1/2 transform -translate-x-1/2 bg-red-500 px-4 py-2 rounded-lg shadow-xl z-50 text-sm';
    notif.textContent = `⚠️ ${message}`;
    document.body.appendChild(notif);
    
    setTimeout(() => notif.remove(), 5000);
}

function toggleTheme() {
    document.body.classList.toggle('light-theme');
}

// ===== Admin: Manual Actual Reentry Recording =====

window.recordActualReentry = function(noradId, dateStr) {
    const date = new Date(dateStr);
    window.LearningSystem.recordActualReentry(noradId, date.getTime(), 'manual');
    refreshData();
    alert('✅ บันทึก re-entry จริงแล้ว ระบบจะเรียนรู้และปรับปรุงความแม่นยำ');
};

window.clearLearningData = function() {
    if (confirm('คุณแน่ใจหรือไม่ที่จะล้างข้อมูลการเรียนรู้ทั้งหมด?')) {
        window.LearningSystem.clearAll();
        refreshData();
        alert('🗑️ ล้างข้อมูลการเรียนรู้แล้ว');
    }
};

// Expose
window.refreshData = refreshData;
window.showTab = showTab;
window.changeSatellite = changeSatellite;
window.toggleTheme = toggleTheme;
