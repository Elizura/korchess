export function register() {
  if (process.env.NODE_ENV === 'production' && !process.env.API_INTERNAL_URL) {
    throw new Error(
      'API_INTERNAL_URL environment variable is required in production. ' +
      'Set it in docker-compose or your deployment environment.'
    );
  }
}
