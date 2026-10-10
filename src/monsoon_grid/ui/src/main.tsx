import React from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource/ibm-plex-sans/latin-400.css';
import '@fontsource/ibm-plex-sans/latin-500.css';
import '@fontsource/ibm-plex-sans/latin-600.css';
import '@fontsource/ibm-plex-mono/latin-400.css';
import './styles.css';
import './panels.css';
import { App } from './App';

class WorkspaceBoundary extends React.Component<React.PropsWithChildren, { failed: boolean; message: string }> {
	state = { failed: false, message: '' };
	static getDerivedStateFromError(error: unknown) { return { failed: true, message: error instanceof Error ? error.message : String(error) }; }
	render() {
		if (this.state.failed) return <main className="connection" role="alert"><h1>VAYU</h1><p>The workspace could not render. No action was issued.</p>{this.state.message && <pre className="hash" style={{ whiteSpace: 'pre-wrap' }}>{this.state.message}</pre>}<button onClick={() => location.reload()}>Reconnect</button><p className="meta">SIMULATION / REPLAY / NO LIVE CONTROL</p></main>;
		return this.props.children;
	}
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><WorkspaceBoundary><App /></WorkspaceBoundary></React.StrictMode>);