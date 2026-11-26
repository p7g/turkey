# The Ruby benchmark server: a bare Rack application served by Puma in
# cluster mode, one process per core. Serves the routes
# bench/http/turkey/Server.gob lists.
#
#     puma -w N -t 16:16 bench/http/ruby/config.ru

HELLO = "Hello, world!\n".b.freeze
CHUNK = ("x" * 16384).b.freeze
MEBIBYTE = ("x" * (16384 * 64)).b.freeze

# A body of 64 pieces with no length, which Puma sends chunked.
class Pieces
  def each
    64.times { yield CHUNK }
  end
end

run(lambda do |env|
  case [env["REQUEST_METHOD"], env["PATH_INFO"]]
  when ["GET", "/hello"]
    [200, { "content-type" => "text/plain", "content-length" => HELLO.bytesize.to_s }, [HELLO]]
  when ["GET", "/1m"]
    [200, { "content-type" => "application/octet-stream", "content-length" => MEBIBYTE.bytesize.to_s }, [MEBIBYTE]]
  when ["POST", "/upload"]
    input = env["rack.input"]
    read = 0
    buf = String.new(capacity: 65536)
    while input.read(65536, buf)
      read += buf.bytesize
    end
    if read > 2 << 20
      [400, { "content-length" => "0" }, []]
    else
      [200, { "content-type" => "text/plain", "content-length" => "3" }, ["ok\n"]]
    end
  when ["GET", "/stream"]
    [200, { "content-type" => "application/octet-stream" }, Pieces.new]
  else
    [404, { "content-length" => "0" }, []]
  end
end)
