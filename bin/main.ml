let usage = "usage: turkey <file>"

let parse_args () =
    let input_file = ref None in
    let handle_arg s = input_file := Some s in
    Arg.parse [] handle_arg usage;
    !input_file

let run_file name =
    let content = In_channel.with_open_text name In_channel.input_all in
    match Turkey.Parser.parse content with
    | Ok ast -> print_endline (Turkey.Ast.show_program ast)
    | Error err ->
      let msg = Turkey.Parser.show_parse_error err in
      Printf.eprintf "%s\n" msg;
      exit 1

let () =
    let file = parse_args () in
    match file with
    | Some f -> run_file f
    | None -> Printf.eprintf "%s\n" usage; exit 1
