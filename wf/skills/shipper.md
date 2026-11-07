# Shipper: ship when the gate is open

Your stage is ship. One command sends the work out: `./wf ship`. A person allows it with one click.

## Steps

1. `./wf status --for shipper` runs the gate.
   - Open: post the ready line it prints.
   - Closed: post the reason it prints, and stop.
2. Run `./wf ship`. A permission card appears in the room. A person clicks Allow once. Wait for it.
3. If the person declines (you see "The user declined this request"), nothing ran. Post exactly this line, with the real task id, and stop:
   `Declined. Nothing left the machine. T<n> stays ready; tell the manager ship when ready.`
   Do not run it again and do not try another way. When the person says ship, the manager posts your hand-off again.
4. After the ship, post what it printed. Then `./wf next`, and post the packet.
   If `./wf ship` refused or failed, post what it printed, and stop.

## Rules

- Run nothing but `./wf ship`, `./wf status`, `./wf next`, `./wf verify`, `./wf skill` and reads.
  Only `./wf ship` sends work out, and only after the gate.
