// ===== Learning System (เก็บข้อมูลใน LocalStorage) =====

const LEARNING_KEY = 'reentry_learning_data';
const PREDICTIONS_KEY = 'reentry_predictions';

class LearningSystem {
    constructor() {
        this.data = this.loadData();
    }
    
    loadData() {
        try {
            const stored = localStorage.getItem(LEARNING_KEY);
            if (stored) return JSON.parse(stored);
        } catch (e) {}
        
        return {
            predictions: [],
            actualReentries: [],
            correctionBias: 0,
            confidenceBoost: 0,
            totalPredictions: 0,
            successfulPredictions: 0,
            lastUpdate: null
        };
    }
    
    saveData() {
        try {
            localStorage.setItem(LEARNING_KEY, JSON.stringify(this.data));
        } catch (e) {
            console.error('Save learning data error:', e);
        }
    }
    
    // บันทึกการทำนาย (เก็บไว้เรียนรู้ในอนาคต)
    recordPrediction(noradId, prediction, tleEpoch) {
        const record = {
            noradId,
            predictedTime: prediction.time,
            confidence: prediction.confidence,
            uncertainty: prediction.uncertainty,
            method: prediction.method || 'ensemble',
            tleEpoch,
            timestamp: Date.now(),
            status: 'pending', // pending, actual_recorded, matched
            actualTime: null
        };
        
        this.data.predictions.push(record);
        this.data.totalPredictions++;
        
        // เก็บสูงสุด 100 รายการ
        if (this.data.predictions.length > 100) {
            this.data.predictions.shift();
        }
        
        this.saveData();
        return record;
    }
    
    // บันทึก re-entry จริง (สำหรับฝึก model)
    recordActualReentry(noradId, actualTime, source = 'manual') {
        const record = {
            noradId,
            actualTime,
            timestamp: Date.now(),
            source
        };
        
        this.data.actualReentries.push(record);
        
        // หาการทำนายที่ใกล้ที่สุด
        this.matchPredictionToActual(noradId, actualTime);
        
        this.saveData();
        this.recalculateBias();
        
        return record;
    }
    
    // จับคู่ prediction กับ actual re-entry
    matchPredictionToActual(noradId, actualTime) {
        const predictions = this.data.predictions.filter(
            p => p.noradId === noradId && p.status === 'pending'
        );
        
        if (predictions.length === 0) return;
        
        // หา prediction ที่ใกล้ที่สุด
        let closest = null;
        let minDiff = Infinity;
        
        predictions.forEach(pred => {
            const diff = Math.abs(pred.predictedTime - actualTime);
            if (diff < minDiff) {
                minDiff = diff;
                closest = pred;
            }
        });
        
        if (closest) {
            closest.status = 'matched';
            closest.actualTime = actualTime;
            closest.error = minDiff; // ms
            
            // ถ้าน้อยกว่า 24 ชม ถือว่า success
            if (minDiff < 24 * 60 * 60 * 1000) {
                this.data.successfulPredictions++;
            }
        }
    }
    
    // คำนวณ bias correction จากประวัติ
    recalculateBias() {
        const matched = this.data.predictions.filter(p => p.status === 'matched');
        
        if (matched.length < 3) {
            // ข้อมูลไม่พอ
            this.data.correctionBias = 0;
            this.data.confidenceBoost = 0;
            return;
        }
        
        // คำนวณ average error (bias)
        const errors = matched.map(m => m.actualTime - m.predictedTime);
        const avgError = errors.reduce((a, b) => a + b, 0) / errors.length;
        
        // คำนวณ success rate
        const successRate = this.data.successfulPredictions / matched.length;
        
        // น้ำหนักเพิ่มขึ้นตามจำนวนข้อมูล
        const weight = Math.min(1, matched.length / 30);
        
        // Apply weighted bias
        this.data.correctionBias = avgError * weight;
        this.data.confidenceBoost = successRate * 15 * weight; // เพิ่ม confidence สูงสุด 15%
        
        this.saveData();
    }
    
    getCorrectionData() {
        return {
            correctionBias: this.data.correctionBias,
            confidenceBoost: this.data.confidenceBoost,
            totalPredictions: this.data.totalPredictions,
            successfulPredictions: this.data.successfulPredictions,
            matchedCount: this.data.predictions.filter(p => p.status === 'matched').length,
            pendingCount: this.data.predictions.filter(p => p.status === 'pending').length,
            accuracy: this.data.totalPredictions > 0 ? 
                (this.data.successfulPredictions / this.data.totalPredictions * 100) : 0,
            learningProgress: Math.min(100, (this.data.predictions.length / 30) * 100)
        };
    }
    
    getLearningHistory() {
        return this.data.predictions
            .slice(-20)
            .reverse()
            .map(p => ({
                ...p,
                predictedDate: new Date(p.predictedTime).toLocaleString('th-TH'),
                errorHours: p.error ? (p.error / (60 * 60 * 1000)).toFixed(1) : null,
                matched: p.status === 'matched'
            }));
    }
    
    // ล้างข้อมูลทั้งหมด
    clearAll() {
        this.data = {
            predictions: [],
            actualReentries: [],
            correctionBias: 0,
            confidenceBoost: 0,
            totalPredictions: 0,
            successfulPredictions: 0,
            lastUpdate: null
        };
        this.saveData();
    }
    
    // Export ข้อมูลสำหรับ backup
    exportData() {
        return JSON.stringify(this.data, null, 2);
    }
    
    // Import ข้อมูล
    importData(jsonStr) {
        try {
            const imported = JSON.parse(jsonStr);
            this.data = { ...this.data, ...imported };
            this.saveData();
            return true;
        } catch (e) {
            return false;
        }
    }
}

// Singleton
window.LearningSystem = new LearningSystem();
