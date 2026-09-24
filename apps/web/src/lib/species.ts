export type Species = {
  id: string;
  scientific_name: string;
  common_names: Record<string, string>;
  custom_names: Record<string, string>;
};

export type NamedCandidate = {
  label: string;
  species_id: string | null;
  scientific_name: string | null;
  common_names: Record<string, string>;
};

export function commonName(species: { common_names: Record<string, string> }, locale: string) {
  return species.common_names[locale] || species.common_names.en || '';
}

/** eBird species page when the code is known, otherwise Wikipedia in the UI language when possible. */
export function speciesPage(species: Species, ebirdCode: string | null, locale: string) {
  if (ebirdCode) return { name: 'eBird', url: `https://ebird.org/species/${encodeURIComponent(ebirdCode)}` };
  const ja = locale === 'ja' && species.common_names.ja;
  return { name: 'Wikipedia', url: ja ? `https://ja.wikipedia.org/wiki/${encodeURIComponent(ja)}`
    : `https://en.wikipedia.org/wiki/${encodeURIComponent(species.scientific_name.replaceAll(' ', '_'))}` };
}

export function candidateName(candidate: NamedCandidate, locale: string) {
  return commonName(candidate, locale) || candidate.scientific_name || candidate.label.replaceAll('_', ' ');
}
