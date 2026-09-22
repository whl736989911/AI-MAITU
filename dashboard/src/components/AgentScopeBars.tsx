/**
 * A page's scope controls: one row per kind of agent the page's content can
 * belong to.
 *
 * Every agent-scoped page draws the experts' bar. A page whose content belongs
 * to an agent of either kind — an automation schedule, the personalization
 * panels, an ACP tool — draws the second one, over the caller's features: a
 * feature's agent is a scope on the same terms an expert's is, and the only
 * difference the two rows have is which half of them each one offers.
 *
 * The two are the *same control* (``AgentSelector``, and the stylesheet that
 * draws it), declared here once so a page cannot get one of them and not the
 * other, or draw its second row out of something else. Both read the one
 * ``activeAgentId``; neither pulls the page off the other half's choice — see
 * the bar's own note.
 *
 * Neither row is told which variant to draw: both take ``AgentSelector``'s own
 * rule, so a page whose half is small enough for a row of chips gets a row of
 * chips on *both* rows and a half too big for one gets a select on both — the
 * same control, drawn the same way, whichever half it is over. The features' row
 * keeps its distance from the row above; nothing else about the two differs.
 *
 * Each row is drawn by its own option set: the bar renders nothing when the
 * caller holds none of that kind, and a bar with nothing in it collapses the
 * slot (``layouts/PageShell.module.less``). A deployment where nobody holds a
 * feature is therefore the single row it has always been — the experts'.
 */

import AgentSelector from "./AgentSelector";

export default function AgentScopeBars() {
  return (
    <>
      <AgentSelector />
      <AgentSelector scope="features" style={{ marginTop: 10 }} />
    </>
  );
}
