import { getDataSource } from '../config/database';
import { Vendor } from '../entities/Vendor';
import { AppError } from '../middleware/errorHandler';

export class VendorService {
  private async getVendorRepository() {
    const dataSource = await getDataSource();
    return dataSource.getRepository(Vendor);
  }

  async findAll(filters: { isActive?: boolean; page?: number; limit?: number }) {
    const vendorRepository = await this.getVendorRepository();
    const page = filters.page || 1;
    const limit = Math.min(filters.limit || 10, 100);
    const skip = (page - 1) * limit;

    const queryBuilder = vendorRepository
      .createQueryBuilder('vendor')
      .orderBy('vendor.name', 'ASC');

    if (filters.isActive !== undefined) {
      queryBuilder.andWhere('vendor.isActive = :isActive', { isActive: filters.isActive });
    }

    const [data, total] = await queryBuilder.skip(skip).take(limit).getManyAndCount();

    return {
      data,
      pagination: {
        page,
        limit,
        total,
        totalPages: Math.ceil(total / limit),
      },
    };
  }

  async findById(id: string) {
    const vendorRepository = await this.getVendorRepository();
    const vendor = await vendorRepository.findOne({
      where: { id },
      relations: ['purchaseOrders'],
    });

    if (!vendor) {
      throw new AppError('Vendor not found', 404);
    }

    return vendor;
  }

  async findByCode(code: string) {
    const vendorRepository = await this.getVendorRepository();
    const vendor = await vendorRepository.findOne({
      where: { code },
    });

    if (!vendor) {
      throw new AppError('Vendor not found', 404);
    }

    return vendor;
  }
}
