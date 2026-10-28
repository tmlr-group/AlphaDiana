#include <iostream>
#include <vector>
#include <string>
#include <algorithm>

using namespace std;

const int MAX_N = 50;
int N;
string h[MAX_N - 1];
string v[MAX_N];
int d[MAX_N][MAX_N];
bool visited[MAX_N][MAX_N];
const int DIJ[4][2] = {{0, 1}, {1, 0}, {0, -1}, {-1, 0}};
const string DIR = "RDLU";

void dfs(int i, int j) {
    visited[i][j] = true;
    for (int dir = 0; dir < 4; ++dir) {
        int di = DIJ[dir][0];
        int dj = DIJ[dir][1];
        int i2 = i + di;
        int j2 = j + dj;
        if (0 <= i2 && i2 < N && 0 <= j2 && j2 < N && !visited[i2][j2]) {
            if (di == 0 && v[i][min(j, j2)] == '0' || dj == 0 && h[min(i, i2)][j] == '0') {
                cout << DIR[dir];
                dfs(i2, j2);
                cout << DIR[(dir + 2) % 4];
            }
        }
    }
}

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    cin >> N;
    for (int i = 0; i < N - 1; ++i) cin >> h[i];
    for (int i = 0; i < N; ++i) cin >> v[i];
    for (int i = 0; i < N; ++i)
        for (int j = 0; j < N; ++j) cin >> d[i][j];
    dfs(0, 0);
    cout << endl;
    return 0;
}
