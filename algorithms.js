// ===== Segmented Moving Average (จากงานวิจัย arxiv 2607.20128) =====

function segmentedMovingAverage(tleHistory, segmentSize = 7) {
    const meanMotions = tleHistory.map(tle => extractMeanMotion(tle.line2));
    const segments = [];
    
    for (let i = 0; i <= meanMotions.length - segmentSize; i++) {
        const segment = meanMotions.slice(i, i + segmentSize);
        const avg = segment.reduce((a, b) => a + b, 0) / segment.length;
        const variance = segment.reduce((sum, val) => sum + Math.pow(val - avg, 2), 0) / segment.length;
        
        segments.push({
            index: i + Math.floor(segmentSize / 2),
            meanMotion: avg,
            stdDev: Math.sqrt(variance),
            variance,
            epoch: tleHistory[i + Math.floor(segmentSize / 2)].epoch
        });
    }
    
    // คำนวณ decay rate (dn/dt)
    if (segments.length >= 2) {
        const first = segments[0];
        const last = segments[segments.length - 1];
        const decayRate = (last.meanMotion - first.meanMotion) / (last.epoch - first.epoch) * (24 * 60 * 60 * 1000); // per day
        return { segments, decayRate, trend: decayRate > 0 ? 'decaying' : 'stable' };
    }
    
    return { segments, decayRate: 0, trend: 'unknown' };
}

// ===== Kalman Filter (1D สำหรับกรอง Mean Motion) =====

class KalmanFilter {
    constructor(initialValue, measurementNoise = 0.001, processNoise = 0.0001) {
        this.x = initialValue;
        this.P = 1.0;
        this.Q = processNoise;
        this.R = measurementNoise;
    }
    
    update(measurement) {
        // Prediction step
        const x_pred = this.x;
        const P_pred = this.P + this.Q;
        
        // Update step
        const K = P_pred / (P_pred + this.R); // Kalman gain
        this.x = x_pred + K * (measurement - x_pred);
        this.P = (1 - K) * P_pred;
        
        return {
            value: this.x,
            uncertainty: Math.sqrt(this.P),
            gain: K
        };
    }
    
    predict(steps = 1) {
        // สำหรับทำนายไปข้างหน้า
        return {
            value: this.x + steps * 0, // assume constant
            uncertainty: Math.sqrt(this.P + steps * this.Q)
        };
    }
}

function applyKalmanFilter(tleHistory) {
    if (tleHistory.length === 0) return [];
    
    const firstMM = extractMeanMotion(tleHistory[0].line2);
    const kf = new KalmanFilter(firstMM, 0.002, 0.0005);
    
    return tleHistory.map(tle => {
        const mm = extractMeanMotion(tle.line2);
        return kf.update(mm);
    });
}

// ===== Extract TLE parameters =====

function extractMeanMotion(line2) {
    return parseFloat(line2.substring(52, 63));
}

function extractInclination(line2) {
    return parseFloat(line2.substring(8, 16));
}

function extractBStar(line1) {
    // Format: ±.DDDDD±D
    const bstarStr = line1.substring(53, 61);
    const mantissa = parseFloat(bstarStr.substring(0, 6));
    const exponent = parseInt(bstarStr.substring(6, 8));
    return mantissa * Math.pow(10, exponent);
}

function extractEccentricity(line2) {
    // Format: .DDDDDDD (no decimal point)
    return parseFloat('0.' + line2.substring(26, 33));
}

// ===== Re-entry Prediction Models =====

// Model 1: Linear Decay
function linearDecayPrediction(segments) {
    if (segments.length < 2) return null;
    
    const last = segments[segments.length - 1];
    const prev = segments[segments.length - 2];
    
    const slope = (last.meanMotion - prev.meanMotion) / (last.epoch - prev.epoch);
    
    // Mean motion ที่ re-entry (ประมาณ 16.0 rev/day สำหรับ altitude ~80km)
    const reentryMM = 16.5;
    
    if (slope >= 0) return null; // ไม่มีการ decay
    
    const timeToReentry = (reentryMM - last.meanMotion) / slope; // ms
    const reentryTime = last.epoch + timeToReentry;
    
    return {
        time: reentryTime,
        confidence: Math.min(80, 100 - last.stdDev * 1000),
        uncertainty: Math.abs(last.stdDev * 24 * 60 * 60 * 1000) // 1 sigma
    };
}

// Model 2: Exponential Decay (แม่นยำกว่า)
function exponentialDecayPrediction(segments) {
    if (segments.length < 3) return null;
    
    const last = segments[segments.length - 1];
    
    // Fit exponential: MM(t) = a * exp(-b*t) + c
    // ใช้ 3 จุดล่าสุด
    const s3 = segments[segments.length - 3];
    const s2 = segments[segments.length - 2];
    const s1 = last;
    
    const t1 = s1.epoch, t2 = s2.epoch, t3 = s3.epoch;
    const y1 = s1.meanMotion, y2 = s2.meanMotion, y3 = s3.meanMotion;
    
    // Simple 2-point exponential fit
    if (y2 === y3 || y1 === y2) return null;
    
    const decayRate = Math.log(y2 / y3) / (t2 - t3);
    
    if (decayRate >= 0) return null;
    
    const reentryMM = 16.5;
    const timeToReentry = Math.log(reentryMM / y1) / decayRate;
    const reentryTime = t1 + timeToReentry;
    
    return {
        time: reentryTime,
        confidence: Math.min(75, 100 - s1.stdDev * 1200),
        uncertainty: Math.abs(s1.stdDev * 30 * 60 * 60 * 1000)
    };
}

// Model 3: Segmented MA (ตามงานวิจัย)
function segmentedMAPrediction(tleHistory) {
    const sma = segmentedMovingAverage(tleHistory, 5);
    
    if (sma.segments.length < 2 || sma.decayRate <= 0) return null;
    
    const last = sma.segments[sma.segments.length - 1];
    const reentryMM = 16.5;
    
    const timeToReentry = (reentryMM - last.meanMotion) / (sma.decayRate / (24 * 60 * 60 * 1000));
    const reentryTime = last.epoch + timeToReentry;
    
    return {
        time: reentryTime,
        confidence: Math.min(85, 100 - last.stdDev * 800),
        uncertainty: Math.abs(last.stdDev * 20 * 60 * 60 * 1000)
    };
}

// ===== Ensemble Prediction =====

function ensemblePrediction(tleHistory) {
    const models = {
        linear: linearDecayPrediction(segmentedMovingAverage(tleHistory, 3).segments),
        exponential: exponentialDecayPrediction(segmentedMovingAverage(tleHistory, 5).segments),
        segmentedMA: segmentedMAPrediction(tleHistory)
    };
    
    // กรอง null
    const validModels = Object.entries(models).filter(([_, pred]) => pred !== null);
    
    if (validModels.length === 0) {
        return null;
    }
    
    if (validModels.length === 1) {
        return { ...validModels[0][1], method: validModels[0][0] };
    }
    
    // Weighted average based on confidence
    let totalTime = 0;
    let totalWeight = 0;
    let maxConfidence = 0;
    
    validModels.forEach(([name, pred]) => {
        const weight = pred.confidence;
        totalTime += pred.time * weight;
        totalWeight += weight;
        maxConfidence = Math.max(maxConfidence, pred.confidence);
    });
    
    const ensembleTime = totalTime / totalWeight;
    
    // คำนวณ uncertainty จาก spread ของ models
    const times = validModels.map(([_, pred]) => pred.time);
    const spread = Math.max(...times) - Math.min(...times);
    
    return {
        time: ensembleTime,
        confidence: maxConfidence * 0.95, // ensemble ลด confidence นิดหน่อย
        uncertainty: Math.max(...validModels.map(([_, pred]) => pred.uncertainty), spread * 0.3),
        method: 'ensemble',
        models: validModels.map(([name, pred]) => ({ name, time: pred.time }))
    };
}

// ===== Apply Learning Correction =====

function applyLearningCorrection(prediction, learningData) {
    if (!learningData || learningData.correctionBias === 0) return prediction;
    
    // ปรับ time ตาม bias ที่เรียนรู้ได้
    const correctedTime = prediction.time + learningData.correctionBias;
    const correctedConfidence = Math.min(95, prediction.confidence + learningData.confidenceBoost);
    
    return {
        ...prediction,
        time: correctedTime,
        confidence: correctedConfidence,
        corrected: true,
        biasApplied: learningData.correctionBias / (60 * 60 * 1000) // hours
    };
}

// ===== Full Prediction Pipeline =====

async function predictReentry(tleHistory, spaceWeather = null, learningData = null) {
    // 1. Apply Kalman Filter
    const filteredData = applyKalmanFilter(tleHistory);
    
    // 2. Run Ensemble Prediction
    let prediction = ensemblePrediction(tleHistory);
    
    if (!prediction) {
        return {
            error: 'ไม่สามารถพยากรณ์ได้: ข้อมูลไม่เพียงพอ',
            time: null,
            confidence: 0
        };
    }
    
    // 3. Apply Space Weather Correction
    if (spaceWeather && spaceWeather.kp > 3) {
        // High Kp = higher density = faster decay
        const kpFactor = 1 + (spaceWeather.kp - 3) * 0.05;
        const adjustedTime = prediction.time - (prediction.time - tleHistory[tleHistory.length-1].epoch) * (kpFactor - 1) * 0.3;
        prediction.time = adjustedTime;
        prediction.confidence *= 0.9; // less confident due to weather
    }
    
    // 4. Apply Learning Correction (if available)
    if (learningData) {
        prediction = applyLearningCorrection(prediction, learningData);
    }
    
    // 5. Calculate additional info
    prediction.tleAge = calculateTLEAge(tleHistory[tleHistory.length-1].epoch);
    prediction.currentAltitude = estimateAltitude(extractMeanMotion(tleHistory[tleHistory.length-1].line2));
    prediction.inclination = extractInclination(tleHistory[tleHistory.length-1].line2);
    prediction.bstar = extractBStar(tleHistory[tleHistory.length-1].line1);
    prediction.filteredData = filteredData;
    
    return prediction;
}

function estimateAltitude(meanMotion) {
    // Approximate altitude from mean motion (for LEO)
    // Using simplified formula: alt ≈ (86666 / MM)^(2/3) - 6378 (km)
    const alt = Math.pow(86666 / meanMotion, 2/3) - 6378;
    return Math.max(0, alt);
}

// Export
window.Algorithms = {
    segmentedMovingAverage,
    KalmanFilter,
    applyKalmanFilter,
    ensemblePrediction,
    predictReentry,
    extractMeanMotion,
    extractInclination,
    extractBStar
};
