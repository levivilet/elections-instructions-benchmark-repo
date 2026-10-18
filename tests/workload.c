#include <unistd.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <stdio.h>
#include <fcntl.h>
static void burn(long n) {
  volatile unsigned long sum = 0;
  for (long i = 0; i < n; i++) sum += i;
}
int main(int argc, char **argv) {
  long n = atol(argv[2]);
  if (argv[1][0] == 'd') {
    if (fork() == 0) {
      setsid();
      if (fork() != 0) _exit(0);
      burn(n);
      int fd = open(argv[3], O_CREAT | O_WRONLY, 0600);
      close(fd);
      _exit(0);
    }
    wait(NULL);
  } else {
    burn(n);
    int fd = open(argv[3], O_CREAT | O_WRONLY, 0600);
    close(fd);
  }
  while (access(argv[4], F_OK) != 0) usleep(1000);
  burn(atol(argv[5]));
  return 0;
}
