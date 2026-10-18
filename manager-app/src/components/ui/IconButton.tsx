import { ButtonProps, Button } from './Button';

export function IconButton(props: ButtonProps & { 'aria-label': string }) {
  return <Button size="icon" variant="ghost" {...props} />;
}
