"""Mock server / replay server for offline testing (placeholder)"""

from http.server import BaseHTTPRequestHandler, HTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{\"status\": \"ok\"}")

if __name__ == '__main__':
    server = HTTPServer(('localhost', 8000), Handler)
    print('Mock server running on http://localhost:8000')
    server.serve_forever()
