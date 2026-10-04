import { Profile } from '../types/profile';

export function groupProfiles(profiles: Profile[], names: string[] = []) {
  const groups = new Map<string, Profile[]>();
  for (const name of names) groups.set(name, []);
  for (const profile of profiles) {
    const name = profile.group?.trim() || '';
    groups.set(name, [...(groups.get(name) || []), profile]);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => a === '' ? 1 : b === '' ? -1 : a.localeCompare(b))
    .map(([name, members]) => ({ name, label: name || 'Ungrouped', members }));
}
