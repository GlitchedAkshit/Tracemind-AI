import { Suspense } from "react";

import { Loading } from "@/components/common";

import { Workspace } from "./workspace";

export default function IncidentPage() {
  return (
    <Suspense fallback={<Loading label="loading incident" />}>
      <Workspace />
    </Suspense>
  );
}
