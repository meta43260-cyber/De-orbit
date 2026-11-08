// ===== Celestrak API (ฟรี, ไม่มี CORS issues) =====

const CELESTRAK_BASE = 'https://celestrak.org/NORAD/elements/gp.php';

async function fetchTLE(noradId) {
    try {
        const url = `${CELESTRAK_BASE}?CATNR=${noradId}&FORMAT=tle`;
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const text = await res.text();
        const lines = text.trim().split('\n');
        
        if (lines.length < 3) throw new Error('Invalid TLE format');
        
        return {
            name: lines[0].trim(),
            line1: lines[1].trim(),
            line2: lines[2].trim(),
            epoch: parseTLEEpoch(lines[1]),
            age: calculateTLEAge(parseTLEEpoch(lines[1]))
        };
    } catch (e) {
        console.error('Fetch TLE error:', e);
        throw e;
    }
}

async function fetchTLEHistory(noradId, days = 30) {
    // ดึง TLE ย้อนหลังจาก Celestrak Archive (ถ้ามี)
    // หมายเหตุ: Celestrak archive มีจำกัด ดังนั้นเราจะจำลองโดยใช้ TLE ปัจจุบัน + simulate
    const history = [];
    const now = new Date();
    
    try {
        // ลองดึง archive ก่อน
        for (let i = 0; i < days; i++) {
            const date = new Date(now - i * 24 * 60 * 60 * 1000);
            const y = date.getUTCFullYear();
            const m = String(date.getUTCMonth() + 1).padStart(2, '0');
            const d = String(date.getUTCDate()).padStart(2, '0');
            
            try {
                const url = `https://celestrak.org/NORAD/archive/tle/${y}/${y}${m}${d}/${noradId}.txt`;
                const res = await fetch(url, { signal: AbortSignal.timeout(2000) });
                if (res.ok) {
                    const lines = (await res.text()).trim().split('\n');
                    if (lines.length >= 3) {
                        history.push({
                            name: lines[0].trim(),
                            line1: lines[1].trim(),
                            line2: lines[2].trim(),
                            epoch: parseTLEEpoch(lines[1])
                        });
                    }
                }
            } catch (e) {
                // skip
            }
            await new Promise(r => setTimeout(r, 100)); // rate limit
        }
    } catch (e) {
        console.error('Fetch history error:', e);
    }
    
    // ถ้าได้ไม่ถึง 5 ตัว ให้ simulate จาก TLE ปัจจุบัน
    if (history.length < 5) {
        const current = await fetchTLE(noradId);
        const simulated = simulateTLEHistory(current, 30);
        return simulated.concat(history).sort((a, b) => a.epoch - b.epoch);
    }
    
    return history.sort((a, b) => a.epoch - b.epoch);
}

function simulateTLEHistory(currentTLE, days) {
    // สร้าง TLE จำลองย้อนหลัง (สำหรับ demo/learning)
    const history = [];
    const currentEpoch = currentTLE.epoch;
    const currentMeanMotion = parseFloat(currentTLE.line2.substring(52, 63));
    
    for (let i = days; i > 0; i--) {
        const daysAgo = i;
        // Simulate: mean motion ลดลงเมื่อเวลาผ่านไป (altitude สูงขึ้นในอดีต)
        const historicalMM = currentMeanMotion - (daysAgo * 0.0005) + (Math.random() - 0.5) * 0.001;
        
        const epoch = new Date(currentEpoch - daysAgo * 24 * 60 * 60 * 1000);
        const dayOfYear = getDayOfYear(epoch);
        const epochStr = `${String(epoch.getUTCFullYear()).slice(2)}${dayOfYear.toFixed(8).padStart(12, '0')}`;
        
        // สร้าง TLE จำลอง
        const line1 = currentTLE.line1.substring(0, 18) + epochStr + currentTLE.line1.substring(32);
        const line2 = currentTLE.line2.substring(0, 52) + historicalMM.toFixed(8).padStart(11, '0') + currentTLE.line2.substring(63);
        
        history.push({
            name: currentTLE.name,
            line1,
            line2,
            epoch: epoch.getTime()
        });
    }
    
    return history;
}

function parseTLEEpoch(line1) {
    // Format: YYDDD.DDDDDDDD
    const epochStr = line1.substring(18, 32);
    const year = parseInt(epochStr.substring(0, 2));
    const fullYear = year > 50 ? 1900 + year : 2000 + year;
    const dayOfYear = parseFloat(epochStr.substring(2));
    
    const date = new Date(fullYear, 0);
    date.setUTCDate(dayOfYear);
    return date.getTime();
}

function getDayOfYear(date) {
    const start = new Date(date.getUTCFullYear(), 0, 0);
    const diff = date - start;
    return diff / (1000 * 60 * 60 * 24);
}

function calculateTLEAge(epochMs) {
    const now = Date.now();
    const ageHours = (now - epochMs) / (1000 * 60 * 60);
    return ageHours;
}

// ===== Space Weather API =====

async function fetchSpaceWeather() {
    try {
        const res = await fetch('https://services.swpc.noaa.gov/json/planetary_k_index_1m.json');
        const data = await res.json();
        const latest = data[data.length - 1];
        return {
            kp: parseFloat(latest[1]),
            timestamp: latest[0]
        };
    } catch (e) {
        return { kp: 2.0, timestamp: new Date().toISOString() }; // default
    }
}

async function fetchSolarFlux() {
    try {
        const res = await fetch('https://services.swpc.noaa.gov/json/solar_flux_fx107_forecast.json');
        const data = await res.json();
        // Get latest
        const latest = data[0];
        return {
            f107: parseFloat(latest.flux),
            timestamp: latest.date
        };
    } catch (e) {
        return { f107: 150, timestamp: new Date().toISOString() }; // default
    }
}

// Export
window.API = {
    fetchTLE,
    fetchTLEHistory,
    fetchSpaceWeather,
    fetchSolarFlux
};
