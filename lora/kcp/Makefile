CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra

liblorakcp.so: shim.c vendor/ikcp.c vendor/ikcp.h
	$(CC) $(CFLAGS) -fPIC -shared shim.c vendor/ikcp.c -o $@

.PHONY: test clean
test: liblorakcp.so
	python3 -m unittest discover -s tests -v
clean:
	rm -f liblorakcp.so
