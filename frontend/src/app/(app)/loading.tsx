import { Loading } from "@/components/common";

// Per-segment Suspense boundary: pages suspend on the client-side session (useSession)
// and stream in behind this placeholder, keeping navigations between them instant.
export default function AppLoading() {
  return <Loading label="loading" />;
}
