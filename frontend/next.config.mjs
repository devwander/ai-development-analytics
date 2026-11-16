/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The dashboard is read-only over the API; no image optimisation needed.
  images: { unoptimized: true },
};

export default nextConfig;
