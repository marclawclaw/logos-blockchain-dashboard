# Context

The words this project uses, and what each one means here. Code, config keys,
tests and docs should use these terms and no synonyms.

## Glossary

**Node**
A Logos process the operator runs on this machine and wants to watch. The
dashboard only ever shows the operator's own nodes, on the machine the
dashboard itself runs on.

**Node kind** (or just *kind*)
What sort of node it is: `blockchain`, `storage` or `delivery`. A machine has
zero or one node of each kind.

**Deployed**
A kind is deployed on this machine if, and only if, its block (`blockchain:`,
`storage:`, `delivery:`) is present in the config. Nothing is probed or
guessed. A kind that is not deployed is not polled, has no section on the page,
and is never an error. Not to be confused with *not answering*.

**Not answering** (status `down`)
A deployed node that could not be read just now: the process is stopped, its
module is crashed or not loaded, the node inside the module is not running, or
the Logos Core daemon cannot be reached. The page keeps the node's last known
values and says when it was last seen.

**Module**
A node that runs inside the Logos Core daemon rather than as its own process.
Storage and delivery are modules (`storage_module`, `delivery_module`). A
module can be *loaded* while the node inside it is *not running*; both must be
true for the node to count as up.

**Logos Core daemon**
The process that hosts modules. It is queried with `logosctl`, the only
supported interface to modules. The blockchain node is not a module; it is
read over its own REST API.

**Host**
The machine itself: CPU temperature, CPU usage, memory, load. Host metrics do
not belong to any node and are recorded whatever the nodes do.

**Reading**
One look at one node: a flat set of values, plus whether the node could be
considered up. A value that could not be read is absent, not zero.

**Snapshot**
A reading the collector stored, aligned to the 10-minute interval. Each kind
and the host have their own snapshot table.

**Gap**
An interval with no snapshot for a kind, because the node was not answering.
Charts draw a gap as a break in the line, never as zero and never as a line
joining the points on either side.

**Counter**
A value that only grows while the node runs and restarts from zero with it
(bytes in, messages in, blocks served to peers). Stored as-is in `*_total`
columns; charted as growth per interval. A counter going down means the node
restarted, which is charted as a gap.

**Current values** (or *live* values)
What the page shows outside the charts: the newest reading of each kind, taken
by the dashboard itself, at most once per refresh period, and only while
someone has the page open.

**Last known values**
The values of the newest successful reading of a node that is now not
answering: from the dashboard's memory, or from the newest snapshot if the
dashboard has not seen the node up since it started.

## Things that are deliberately not concepts here

- **Network-wide stats.** Only what the operator's own node reports.
- **Several nodes of one kind.** Zero or one of each.
- **Alerting.** The page shows state; it does not notify.
- **Files served.** The storage node counts blocks sent to peers, not files.
  "Blocks served to peers" is the serving measure; "files stored" is the number
  of manifests held locally.
