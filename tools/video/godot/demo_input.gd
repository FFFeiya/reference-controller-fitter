extends Node
# Demo helper (autoload): presses the same input actions a player would (ui_left / ui_right /
# ui_accept) following a scripted timeline, logs the player for the on-screen key display,
# and quits when done. Not needed in a real game.

var events := []
var next := 0
var t := 0.0
var duration := 13.0
var axis := 0
var jump := false
var log_lines := PackedStringArray(["frame,t,axis,jump,x,y"])
var frame := 0


func _ready():
	process_priority = -100
	process_physics_priority = -100
	var f := FileAccess.open("res://demo/events.txt", FileAccess.READ)
	while not f.eof_reached():
		var p := f.get_line().split(" ")
		if p.size() >= 3:
			events.append([float(p[0]), int(p[1]), p[2] == "1"])
	var d := OS.get_environment("RCF_DURATION")
	if d != "":
		duration = float(d)


func _physics_process(delta):
	while next < events.size() and events[next][0] <= t + 1e-4:
		axis = events[next][1]
		jump = events[next][2]
		next += 1
	_press("ui_right", axis > 0)
	_press("ui_left", axis < 0)
	_press("ui_accept", jump)
	t += delta


func _press(action: String, on: bool):
	if on and not Input.is_action_pressed(action):
		Input.action_press(action)
	elif not on and Input.is_action_pressed(action):
		Input.action_release(action)


func _process(_delta):
	var player = get_tree().current_scene.get_node_or_null("Player")
	if player:
		var ppu: float = player.pixels_per_unit
		log_lines.append("%d,%.4f,%d,%d,%.4f,%.4f" % [frame, t, axis, int(jump), player.position.x / ppu,
			-(player.position.y + 0.5 * ppu) / ppu])
	frame += 1
	if t >= duration:
		var out := OS.get_environment("RCF_LOG")
		if out != "":
			var f := FileAccess.open(out, FileAccess.WRITE)
			f.store_string("\n".join(log_lines))
			f.close()
		get_tree().quit()
