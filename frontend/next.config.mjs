/** @type {import('next').Config} */
const nextConfig = {
  output: 'standalone',
  experimental: {
    // Disable build-time enforcement of wrapping useSearchParams in a Suspense
    // boundary for CSR pages like the home route. This avoids the
    // "useSearchParams() should be wrapped in a suspense boundary" error
    // when building for production.
    missingSuspenseWithCSRBailout: false,
  },
};

export default nextConfig;
