// The Java benchmark server: the JDK's own com.sun.net.httpserver, with a
// virtual thread per exchange: `java Server PORT [KEYSTORE.p12]`. Run it with
// -XX:ActiveProcessorCount=N, which sizes the carrier threads under the
// virtual threads and the collector's threads. Serves the routes
// bench/http/turkey/Server.gob lists.

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import com.sun.net.httpserver.HttpsConfigurator;
import com.sun.net.httpserver.HttpsServer;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.security.KeyStore;
import java.util.Arrays;
import java.util.concurrent.Executors;
import javax.net.ssl.KeyManagerFactory;
import javax.net.ssl.SSLContext;

public class Server {
    static final byte[] HELLO = "Hello, world!\n".getBytes();
    static final byte[] CHUNK = filled(16384);
    static final byte[] MEBIBYTE = filled(16384 * 64);

    static byte[] filled(int n) {
        byte[] b = new byte[n];
        Arrays.fill(b, (byte) 'x');
        return b;
    }

    static void handle(HttpExchange ex) throws IOException {
        String method = ex.getRequestMethod();
        String path = ex.getRequestURI().getPath();
        try (ex) {
            if (method.equals("GET") && path.equals("/hello")) {
                fixed(ex, 200, "text/plain", HELLO);
            } else if (method.equals("GET") && path.equals("/1m")) {
                fixed(ex, 200, "application/octet-stream", MEBIBYTE);
            } else if (method.equals("POST") && path.equals("/upload")) {
                InputStream in = ex.getRequestBody();
                byte[] buf = new byte[65536];
                long read = 0;
                for (int n; (n = in.read(buf)) > 0; ) read += n;
                if (read > 2 << 20) {
                    fixed(ex, 400, "text/plain", new byte[0]);
                } else {
                    fixed(ex, 200, "text/plain", "ok\n".getBytes());
                }
            } else if (method.equals("GET") && path.equals("/stream")) {
                ex.getResponseHeaders().set("Content-Type", "application/octet-stream");
                ex.sendResponseHeaders(200, 0);
                OutputStream out = ex.getResponseBody();
                for (int i = 0; i < 64; i++) out.write(CHUNK);
            } else {
                ex.sendResponseHeaders(404, -1);
            }
        }
    }

    static void fixed(HttpExchange ex, int status, String type, byte[] body) throws IOException {
        ex.getResponseHeaders().set("Content-Type", type);
        ex.sendResponseHeaders(status, body.length == 0 ? -1 : body.length);
        ex.getResponseBody().write(body);
    }

    public static void main(String[] args) throws Exception {
        InetSocketAddress at = new InetSocketAddress("127.0.0.1", Integer.parseInt(args[0]));
        HttpServer server;
        if (args.length == 2) {
            KeyStore ks = KeyStore.getInstance("PKCS12");
            ks.load(new FileInputStream(args[1]), "bench".toCharArray());
            KeyManagerFactory kmf = KeyManagerFactory.getInstance("SunX509");
            kmf.init(ks, "bench".toCharArray());
            SSLContext ssl = SSLContext.getInstance("TLS");
            ssl.init(kmf.getKeyManagers(), null, null);
            HttpsServer s = HttpsServer.create(at, 4096);
            s.setHttpsConfigurator(new HttpsConfigurator(ssl));
            server = s;
        } else {
            server = HttpServer.create(at, 4096);
        }
        server.createContext("/", Server::handle);
        server.setExecutor(Executors.newVirtualThreadPerTaskExecutor());
        server.start();
    }
}
