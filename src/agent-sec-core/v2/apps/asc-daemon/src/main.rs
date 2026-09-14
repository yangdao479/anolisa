use std::process::ExitCode;
use std::sync::Arc;
use std::time::Duration;

mod sinks;

use asc_action_runtime::Finalizer;
use asc_daemon::{Cli, ParseOutcome, ProcessSignals, run_with_shutdown_timeout, serve};
use asc_daemon_core::{PrincipalPolicy, RootManagedPrincipalPolicy};
use asc_daemon_handler::{DaemonDispatcher, JsonRejectionEncoder};
use asc_daemon_service::ShutdownToken;
use asc_event_sink::ConfiguredSecurityEventSinks;
use asc_pap::PapService;
use asc_pap_repository_memory::ProcessLocalPapRepository;
use asc_policy_engine::PolicyTemplateCompiler;
use asc_security_events::config::{get_db_path, get_log_path};

use crate::sinks::{EventSinkAdapter, NoopEventSink};

const RUNTIME_SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(1);

fn main() -> ExitCode {
    match run_with_shutdown_timeout(run(), RUNTIME_SHUTDOWN_TIMEOUT) {
        Ok((exit_code, event_sinks)) => {
            if let Some(sinks) = event_sinks {
                sinks.close();
            }
            exit_code
        }
        Err(problem) => {
            report_error(&problem);
            ExitCode::FAILURE
        }
    }
}

async fn run() -> (ExitCode, Option<Arc<ConfiguredSecurityEventSinks>>) {
    let outcome = match Cli::parse_from(std::env::args_os()) {
        Ok(outcome) => outcome,
        Err(problem) => {
            eprintln!("agent-sec-daemon: {problem}");
            return (ExitCode::from(2), None);
        }
    };
    let ParseOutcome::Serve(cli) = outcome else {
        let ParseOutcome::Help(help) = outcome else {
            unreachable!("all parse outcomes are covered")
        };
        print!("{help}");
        return (ExitCode::SUCCESS, None);
    };

    let signals = match ProcessSignals::install() {
        Ok(signals) => signals,
        Err(problem) => {
            eprintln!("agent-sec-daemon: {problem}");
            return (ExitCode::FAILURE, None);
        }
    };
    let repository = Arc::new(ProcessLocalPapRepository::default());
    let pap = PapService::new(repository, Arc::new(PolicyTemplateCompiler));
    let principal_policy = Arc::new(RootManagedPrincipalPolicy::with_admin_uids(
        cli.policy_admin_uids,
    ));
    let policy_for_handler: Arc<dyn PrincipalPolicy> = principal_policy.clone();
    let (finalizer, event_sinks) = event_finalizer();
    let dispatcher = Arc::new(DaemonDispatcher::new_with_finalizer(
        pap,
        policy_for_handler,
        finalizer,
    ));
    eprintln!("agent-sec-daemon: warning: PAP state is process-local and is lost on restart");

    let shutdown = ShutdownToken::new();
    let signal_task = tokio::spawn(signals.request_shutdown(shutdown.clone()));
    let result = serve(
        cli.bootstrap,
        dispatcher,
        Arc::new(JsonRejectionEncoder),
        shutdown,
    )
    .await;
    signal_task.abort();
    let exit_code = match result {
        Ok(_) => ExitCode::SUCCESS,
        Err(problem) => {
            report_error(&problem);
            ExitCode::FAILURE
        }
    };
    (exit_code, event_sinks)
}

fn event_finalizer() -> (Finalizer, Option<Arc<ConfiguredSecurityEventSinks>>) {
    match (get_log_path(), get_db_path()) {
        (Ok(jsonl_path), Ok(sqlite_path)) => {
            let sinks = Arc::new(ConfiguredSecurityEventSinks::new(jsonl_path, sqlite_path));
            if let Err(error) = sinks.warm_jsonl() {
                eprintln!("agent-sec-daemon: security event JSONL sink unavailable: {error}");
            }
            if let Err(error) = sinks.warm_sqlite() {
                eprintln!("agent-sec-daemon: security event SQLite sink unavailable: {error}");
            }
            (
                Finalizer::new(Arc::new(EventSinkAdapter::new(Arc::clone(&sinks)))),
                Some(sinks),
            )
        }
        (Err(error), _) | (_, Err(error)) => {
            eprintln!("agent-sec-daemon: security event paths unavailable: {error}");
            (Finalizer::new(Arc::new(NoopEventSink)), None)
        }
    }
}

fn report_error(problem: &dyn std::error::Error) {
    eprintln!("agent-sec-daemon: {problem}");
    let mut source = problem.source();
    while let Some(cause) = source {
        eprintln!("  caused by: {cause}");
        source = cause.source();
    }
}
