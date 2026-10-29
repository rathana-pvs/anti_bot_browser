import { Monitor, Smartphone } from 'lucide-react';
import { Profile } from '../../types/profile';
import { AndroidDevice } from '../../types/device';
import { cn } from '../ui/cn';
import { deviceLabel, isAndroid } from './accountModel';

/** The device an account lives on. Violet is always the PC, teal always a phone. */
export function DeviceChip({ profile, devices, onClick, className }: {
  profile: Profile; devices: AndroidDevice[]; onClick?: () => void; className?: string;
}) {
  const phone = isAndroid(profile);
  const Icon = phone ? Smartphone : Monitor;
  const classes = cn('inline-flex max-w-full items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[11px]',
    phone ? 'bg-teal-500/10 text-teal-300' : 'bg-violet-500/10 text-violet-300',
    onClick && 'hover:underline underline-offset-2', className);
  const content = <><Icon className="h-3 w-3 shrink-0" aria-hidden="true" /><span className="truncate">{deviceLabel(profile, devices)}</span></>;
  return onClick
    ? <button type="button" title="Show this device on the Devices page" className={classes}
      onClick={(event) => { event.stopPropagation(); onClick(); }}>{content}</button>
    : <span className={classes}>{content}</span>;
}
