import { Router } from 'express';
import { aiInsightsController } from '../controllers/aiInsightsController';

const router = Router();

// AI Insights routes
router.get('/insights', (req, res) => aiInsightsController.getAllInsights(req, res));
router.get('/summary', (req, res) => aiInsightsController.getSummary(req, res));
router.get('/predictions', (req, res) => aiInsightsController.getPredictions(req, res));
router.get('/anomalies', (req, res) => aiInsightsController.getAnomalies(req, res));
router.get('/recommendations', (req, res) => aiInsightsController.getRecommendations(req, res));

export default router;
