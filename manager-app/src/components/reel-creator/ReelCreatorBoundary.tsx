import { Component, type ReactNode } from 'react';
import { Button } from '../ui/Button';

/** A reel display error must not remove the rest of the desktop application. */
export class ReelCreatorBoundary extends Component<{ children: ReactNode }, { error: boolean; attempt: number }> {
  state = { error: false, attempt: 0 };
  static getDerivedStateFromError() { return { error: true }; }
  render() {
    if (this.state.error) return <div role="alert" className="space-y-3 p-6 text-sm text-zinc-300">
      <p>The reel editor could not display. Your saved drafts are still available.</p>
      <Button variant="outline" onClick={() => this.setState(state => ({ error: false, attempt: state.attempt + 1 }))}>Reopen reel editor</Button>
    </div>;
    return <div className="h-full min-h-0" key={this.state.attempt}>{this.props.children}</div>;
  }
}
