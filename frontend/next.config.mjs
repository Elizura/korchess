/** @type {import('next').Config} */

const API_INTERNAL_URL = process.env.API_INTERNAL_URL;

if (!API_INTERNAL_URL && process.env.NODE_ENV === 'production') {
  throw new Error(
    "API_INTERNAL_URL environment variable is required in production. " +
    "Set it in docker-compose or your deployment environment."
  );
}

const nextConfig = {
  output: 'standalone',
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${API_INTERNAL_URL || 'http://localhost:8000'}/api/:path*`,
      },
    ];
  },
  experimental: {
    missingSuspenseWithCSRBailout: false,
  },
};

export default nextConfig;
