@tool
extends EditorPlugin
# Drives the real Godot editor through the demo steps for the project video. After each step
# it writes ready_<n>.txt into RCF_SIGNAL and waits for shot_<n>.txt from the recorder.

var steps := ["script", "scene", "player", "running"]
var step := -1
var waiting := false
var wait_until := 0.0
var signal_dir := ""
var clock := 0.0


func _enter_tree():
	signal_dir = OS.get_environment("RCF_SIGNAL")
	if signal_dir == "":
		return
	wait_until = 6.0
	set_process(true)


func _process(delta):
	if signal_dir == "":
		return
	clock += delta
	if clock < wait_until:
		return
	if waiting:
		if not FileAccess.file_exists(signal_dir.path_join("shot_%d.txt" % step)):
			return
		waiting = false
	step += 1
	if step >= steps.size():
		if EditorInterface.is_playing_scene():
			EditorInterface.stop_playing_scene()
		var f := FileAccess.open(signal_dir.path_join("finished.txt"), FileAccess.WRITE)
		f.store_string("ok")
		f.close()
		set_process(false)
		get_tree().quit()
		return
	_do(steps[step])
	wait_until = clock + (5.0 if steps[step] == "running" else 2.0)
	waiting = true
	# the ready file is written once the wait is over (next frames)
	await get_tree().create_timer(4.5 if steps[step] == "running" else 1.5).timeout
	var r := FileAccess.open(signal_dir.path_join("ready_%d.txt" % step), FileAccess.WRITE)
	r.store_string(steps[step])
	r.close()


func _do(name: String):
	match name:
		"script":
			# the exported script dropped into the project, opened in the script editor
			var s = load("res://fitted_platformer_controller.gd")
			EditorInterface.get_file_system_dock().navigate_to_path("res://fitted_platformer_controller.gd")
			EditorInterface.edit_script(s)
			EditorInterface.set_main_screen_editor("Script")
		"scene":
			EditorInterface.open_scene_from_path("res://main.tscn")
			EditorInterface.set_main_screen_editor("2D")
			var root = EditorInterface.get_edited_scene_root()
			EditorInterface.get_selection().clear()
			EditorInterface.get_selection().add_node(root.get_node("Level/Step 2"))
		"player":
			var root = EditorInterface.get_edited_scene_root()
			var player = root.get_node("Player")
			EditorInterface.get_selection().clear()
			EditorInterface.get_selection().add_node(player)
			EditorInterface.edit_node(player)
		"running":
			EditorInterface.play_main_scene()
