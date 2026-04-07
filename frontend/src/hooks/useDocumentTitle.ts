import { useEffect } from "react";

const BASE_TITLE = "Wiz Scheduler";

export function useDocumentTitle(page?: string) {
  useEffect(() => {
    document.title = page ? `${page} | ${BASE_TITLE}` : BASE_TITLE;
    return () => {
      document.title = BASE_TITLE;
    };
  }, [page]);
}
