import bcrypt from 'bcrypt';
import { getDataSource } from '../config/database';
import { User } from '../entities/User';
import { generateAccessToken, generateRefreshToken, verifyRefreshToken } from '../utils/jwt';
import { AppError } from '../middleware/errorHandler';

export class AuthService {
  private async getUserRepository() {
    const dataSource = await getDataSource();
    return dataSource.getRepository(User);
  }

  async login(username: string, password: string) {
    const userRepository = await this.getUserRepository();
    const user = await userRepository.findOne({ where: { username } });

    if (!user) {
      throw new AppError('Invalid credentials', 401);
    }

    if (!user.isActive) {
      throw new AppError('Account is inactive', 403);
    }

    const isPasswordValid = await bcrypt.compare(password, user.passwordHash);

    if (!isPasswordValid) {
      throw new AppError('Invalid credentials', 401);
    }

    const accessToken = generateAccessToken(user);
    const refreshToken = generateRefreshToken(user);

    return {
      user: {
        id: user.id,
        username: user.username,
        name: user.name,
        email: user.email,
        role: user.role,
      },
      accessToken,
      refreshToken,
    };
  }

  async refresh(refreshToken: string) {
    try {
      const payload = verifyRefreshToken(refreshToken);

      const userRepository = await this.getUserRepository();
      const user = await userRepository.findOne({ where: { id: payload.userId } });

      if (!user || !user.isActive) {
        throw new AppError('Invalid refresh token', 401);
      }

      const newAccessToken = generateAccessToken(user);
      const newRefreshToken = generateRefreshToken(user);

      return {
        accessToken: newAccessToken,
        refreshToken: newRefreshToken,
      };
    } catch (error) {
      throw new AppError('Invalid or expired refresh token', 401);
    }
  }

  async logout(refreshToken: string) {
    // In a production system, you would invalidate the refresh token here
    // For now, we'll just verify it's valid
    try {
      verifyRefreshToken(refreshToken);
      return { message: 'Logged out successfully' };
    } catch (error) {
      throw new AppError('Invalid refresh token', 401);
    }
  }
}
