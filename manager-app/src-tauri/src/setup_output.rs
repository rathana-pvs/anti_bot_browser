//! Keep a bounded summary of this setup process, including failures before logging starts.
use std::collections::VecDeque;

const MAX_LINES: usize = 200;

#[derive(Default)]
pub struct SetupOutput {
    lines: VecDeque<String>,
    completed: bool,
}

impl SetupOutput {
    pub fn record(&mut self, line: &str) {
        let line = line.trim();
        if line == "READY: Installation completed."
            || line == "READY: Automat FB Beta installation completed successfully."
        {
            self.completed = true;
        }
        if !line.is_empty() {
            if self.lines.len() == MAX_LINES {
                self.lines.pop_front();
            }
            self.lines.push_back(line.chars().take(2048).collect());
        }
    }

    pub fn completed(&self) -> bool {
        self.completed
    }

    pub fn failure_message(&self, code: i32) -> String {
        let detail = self.lines.iter().rev().find(|line| {
            let lower = line.to_lowercase();
            !lower.starts_with("warning")
                && (lower.starts_with("error")
                    || lower.starts_with("e:")
                    || lower.starts_with("fatal:")
                    || lower.starts_with("failed ")
                    || lower.contains("error:")
                    || lower.contains("error while loading shared libraries")
                    || lower.contains("permission denied")
                    || lower.contains("no space left on device"))
        });
        match detail {
            Some(line) => format!(
                "Setup failed (exit code {code}): {}",
                line.chars().take(300).collect::<String>()
            ),
            None => format!(
                "Setup stopped with exit code {code}. Open Setup activity for the failed step."
            ),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn package_error_survives_shell_failure_footer() {
        let mut output = SetupOutput::default();
        output.record("ERROR: Could not find a version that satisfies the requirement example==1.0");
        output.record("Installation stopped at line 382 (exit 1).");
        output.record("Review data/install.log, fix the reported issue, then run repair");
        assert!(output.failure_message(1).contains("Could not find a version"));
        assert!(!output.completed());
    }

    #[test]
    fn captures_system_package_and_interpreter_errors_before_file_logging() {
        for line in [
            "E: Unable to locate package example",
            "Error executing command as another user: Request dismissed",
            "ModuleNotFoundError: No module named 'example'",
            "/bin/bash: /read-only/install.sh: Permission denied",
        ] {
            let mut output = SetupOutput::default();
            output.record(line);
            assert!(output.failure_message(1).contains(line));
        }
    }

    #[test]
    fn only_current_process_can_report_completion() {
        let mut previous = SetupOutput::default();
        previous.record("READY: Automat FB Beta installation completed successfully.");
        assert!(previous.completed());
        let mut current = SetupOutput::default();
        current.record("ERROR: Administrator approval was cancelled.");
        assert!(!current.completed());
        assert!(current.failure_message(1).contains("approval was cancelled"));
    }

    #[test]
    fn capture_is_bounded_and_does_not_mistake_warnings_for_the_failure() {
        let mut output = SetupOutput::default();
        for _ in 0..1000 {
            output.record("Downloading a package");
        }
        output.record("ERROR: Not enough disk space.");
        output.record("WARNING: CUDA runtime installation failed; trying CPU instead.");
        assert_eq!(output.lines.len(), MAX_LINES);
        assert!(output.failure_message(1).contains("Not enough disk space"));
        assert!(SetupOutput::default().failure_message(1).contains("Open Setup activity"));
    }
}
