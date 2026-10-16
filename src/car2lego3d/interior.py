"""Native cabin modules fitted inside the reserved glass and roof envelope."""
import math

from .carfit import QUARTER


def furnish_cabin(car, start, end, sides, glass_top):
    left, right = sides
    inner = range(left+1, right)
    center = (left+right)/2
    # Two native two-wide seats leave a console aisle in the eight-wide cabin.
    lanes = (left+1.5, right-1.5)
    seat_height = min(11, glass_top-7)
    rows = [("front seats", start+.5)]
    if end-start >= 7:
        rows.append(("rear seats", end-3.5))
    for module, x in rows:
        for y in lanes:
            xs = [int(x-.5), int(x+.5)]
            ys = [int(y-.5), int(y+.5)]
            car.fill(xs, ys, range(5, seat_height-1), 0)
            car.place("4079", 72, x, y, seat_height, module, 15, QUARTER)
    dashboard_x = start-4
    car.fill([dashboard_x], inner, range(5, seat_height), 0)
    car.tiles([dashboard_x], inner, seat_height+1, 0, "dashboard", 15)
    steering_x = start-2
    steering_height = seat_height-3
    car.fill([steering_x], [left+1, left+2], range(5, steering_height-1), 0)
    car.place("3829c01", 0, steering_x, lanes[0], steering_height, "steering wheel", 15, QUARTER)
    if len(inner) >= 6:
        console_xs = range(start, int(rows[0][1]+1.5))
        console_ys = [int(center-.5), int(center+.5)]
    else:
        last = math.floor(rows[1][1]-1.5) if len(rows)>1 else end-1
        console_xs = range(math.ceil(rows[0][1]+1.75), last+1)
        console_ys = [int(center-.5), int(center+.5)]
    car.fill(console_xs, console_ys, range(5, 7), 0)
    car.tiles(console_xs, console_ys, 8, 72, "cabin console", 15)
    car.fit["interior_fitting"] = dict(
        method="native seat modules inside the reserved cabin", seat_part="4079",
        seat_count=len(rows)*len(lanes), row_count=len(rows),
        seat_top_plates=seat_height+5, glass_top_plates=glass_top,
        dashboard=True, steering_wheel=True, console=bool(console_xs),
        seating_simplified=True)
