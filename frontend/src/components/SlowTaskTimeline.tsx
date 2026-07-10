import type { SlowSystemScenario, SlowTaskTimelineEvent } from "../slowSystem";

type SlowTaskTimelineProps = Readonly<{
  scenario: SlowSystemScenario;
  selectedEvent: SlowTaskTimelineEvent;
  onSelectEvent: (eventId: string) => void;
}>;

function eventLabel(event: SlowTaskTimelineEvent) {
  if (event.identity.canonical) {
    return event.identity.eventName;
  }

  return event.identity.displayLabel;
}

export function SlowTaskTimeline({
  scenario,
  selectedEvent,
  onSelectEvent,
}: SlowTaskTimelineProps) {
  return (
    <section className="panel timeline-panel" aria-labelledby="timeline-heading">
      <div className="panel-heading">
        <div>
          <p className="panel-kicker">SlowTask timeline</p>
          <h2 id="timeline-heading">Mock Event Flow</h2>
        </div>
        <span className="status-pill">{scenario.slowTask.lifecycleState}</span>
      </div>

      <ol className="timeline-list">
        {scenario.timeline.map((event) => {
          const active = event.id === selectedEvent.id;

          return (
            <li key={event.id}>
              <button
                className="timeline-button"
                type="button"
                aria-pressed={active}
                onClick={() => onSelectEvent(event.id)}
              >
                <span className="timeline-order">{event.order}</span>
                <span>
                  <strong>{eventLabel(event)}</strong>
                  <small>{event.summary}</small>
                </span>
              </button>
            </li>
          );
        })}
      </ol>

      <article className="event-detail" aria-live="polite">
        <div className="detail-header">
          <div>
            <p className="panel-kicker">Selected event</p>
            <h3>{eventLabel(selectedEvent)}</h3>
          </div>
          <span className={selectedEvent.identity.canonical ? "badge good" : "badge caution"}>
            {selectedEvent.identity.canonical ? "canonical" : "non-canonical UI label"}
          </span>
        </div>

        {!selectedEvent.identity.canonical ? (
          <p className="noncanonical-note">
            {selectedEvent.identity.nonCanonicalReason}
          </p>
        ) : null}

        <dl className="event-facts">
          <div>
            <dt>owner</dt>
            <dd>{selectedEvent.owner}</dd>
          </div>
          {selectedEvent.routerDecision ? (
            <div>
              <dt>router_decision</dt>
              <dd>{selectedEvent.routerDecision}</dd>
            </div>
          ) : null}
          {selectedEvent.planVersion ? (
            <div>
              <dt>plan_version</dt>
              <dd>
                {selectedEvent.planVersion.value} ({selectedEvent.planVersion.label})
              </dd>
            </div>
          ) : null}
          {selectedEvent.taskEventSeq ? (
            <div>
              <dt>task_event_seq</dt>
              <dd>
                {selectedEvent.taskEventSeq.value} ({selectedEvent.taskEventSeq.label})
              </dd>
            </div>
          ) : null}
        </dl>

        <ul className="detail-list">
          {selectedEvent.details.map((detail) => (
            <li key={detail}>{detail}</li>
          ))}
        </ul>
      </article>
    </section>
  );
}
