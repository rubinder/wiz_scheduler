/** "$18" for a whole-dollar price, "$18.50" otherwise. Always USD. */
export function formatArchivePrice(priceUsd: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: Number.isInteger(priceUsd) ? 0 : 2,
  }).format(priceUsd);
}
