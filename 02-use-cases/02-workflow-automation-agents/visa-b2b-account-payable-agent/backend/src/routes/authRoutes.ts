import { Router } from 'express';
import * as authController from '../controllers/authController';
import { authLimiter } from '../middleware/rateLimiter';

const router = Router();

router.post('/login', authLimiter, authController.loginValidation, authController.login);
router.post('/refresh', authController.refreshValidation, authController.refresh);
router.post('/logout', authController.logout);

export default router;
